"""P5-T5 (provider-neutral core): channel state machine, circuit breaker and global pause.

Exit criterion covered here: the **circuit breaker** pauses new orders within 60 seconds of
`needs_qr`. Also: the global pause blocks new orders for every tenant; orders reopen when
the channel reconnects; retired channels never change; older reports are ignored.

Detection here comes from the FakeProvider through the interface (health poll, session
events, send errors). Detection from real OpenWA webhooks and polls is P5-T2, after the Go.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import psycopg
import pytest
from psycopg import errors

from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env, ScopedDispatcher, Shop, new_live_shop, new_shop, rows
from velora.admin.onboarding import Onboarding, OnboardingError
from velora.clock import FakeClock
from velora.messaging import channels
from velora.messaging.channels import HEALTH_POLL_INTERVAL, ChannelMonitor
from velora.messaging.inbox import InboxProcessor, Ingress
from velora.messaging.outbox import OutboxSender
from velora.messaging.provider import SessionState
from velora.ordering import gate
from velora.workers.runner import Worker


async def gate_of(env: Env, shop: Shop) -> tuple[bool, str | None]:
    async with env.db.tenant(shop.tenant) as conn:
        decision = await gate.check_new_cart(conn, shop.tenant, env.clock.now())
    return decision.open, decision.reason


def worker(env: Env, *shops: Shop) -> Worker:
    return Worker(
        ScopedDispatcher(env.dispatcher, *shops),
        InboxProcessor(env.db, env.clock, {}),
        OutboxSender(env.db, env.providers, env.clock),
        ChannelMonitor(env.db, env.providers, env.clock),
        env.clock,
    )


def test_circuit_breaker_pauses_new_orders_within_60_seconds_of_needs_qr(
    run: Any, db: PgDatabase, clock: FakeClock
) -> None:
    shop = new_live_shop(db)
    lost_at = clock.now() + timedelta(seconds=1)

    async def scenario(env: Env) -> list[tuple[bool, str | None]]:
        loop = worker(env, shop)
        await loop.run_round()  # health poll at t0: connected
        seen = [await gate_of(env, shop)]
        clock.advance(lost_at - clock.now())
        env.fake.set_state(shop.session_ref, "needs_qr")  # the phone is unlinked
        # Worker rounds run every second; the poll is due 60 s after the previous one.
        while clock.now() < lost_at + HEALTH_POLL_INTERVAL:
            clock.advance(timedelta(seconds=1))
            await loop.run_round()
            seen.append(await gate_of(env, shop))
            if not seen[-1][0]:
                break
        return seen

    seen = run(scenario)
    assert seen[0] == (True, None)
    assert seen[-1] == (False, "channel_unavailable")
    ((changed_at,),) = rows(
        db, "SELECT state_changed_at FROM velora.channels WHERE id = %s", shop.channel_id
    )
    assert changed_at - lost_at <= timedelta(seconds=60)
    assert rows(
        db,
        "SELECT details->>'from', details->>'to', details->>'source' FROM velora.channel_events "
        "WHERE channel_id = %s AND event_type = 'state_changed'",
        shop.channel_id,
    ) == [("connected", "needs_qr", "poll")]


def test_a_session_event_trips_the_breaker_at_once_and_reconnecting_reopens(
    run: Any, db: PgDatabase
) -> None:
    shop = new_live_shop(db)

    async def scenario(env: Env) -> list[tuple[bool, str | None]]:
        ingress = Ingress(env.db, env.clock)
        seen = [await gate_of(env, shop)]
        env.clock.advance(timedelta(seconds=1))
        state = SessionState(shop.session_ref, "needs_qr", env.clock.now())
        await ingress.receive("fake", shop.session_ref, [state])
        seen.append(await gate_of(env, shop))
        env.clock.advance(timedelta(minutes=3))
        back = SessionState(shop.session_ref, "connected", env.clock.now())
        await ingress.receive("fake", shop.session_ref, [back])
        seen.append(await gate_of(env, shop))
        return seen

    assert run(scenario) == [(True, None), (False, "channel_unavailable"), (True, None)]


@pytest.mark.parametrize("state", ["reconnecting", "logged_out", "failed", "suspected_restricted"])
def test_every_down_state_trips_the_breaker(run: Any, db: PgDatabase, state: Any) -> None:
    shop = new_live_shop(db)

    async def scenario(env: Env) -> tuple[bool, str | None]:
        env.clock.advance(timedelta(seconds=1))
        env.fake.set_state(shop.session_ref, state)
        await ChannelMonitor(env.db, env.providers, env.clock).poll(shop.tenant)
        return await gate_of(env, shop)

    assert run(scenario) == (False, "channel_unavailable")


def test_an_unreachable_provider_marks_the_channel_failed(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        env.fake.status_error = ConnectionError("connect to http://openwa:2785 token=abc12345xyz")
        env.clock.advance(timedelta(seconds=1))
        await ChannelMonitor(env.db, env.providers, env.clock).poll(shop.tenant)

    run(scenario)
    ((state, detail),) = rows(
        db,
        "SELECT c.state, e.details->>'detail' FROM velora.channels c "
        "JOIN velora.channel_events e ON e.channel_id = c.id WHERE c.id = %s",
        shop.channel_id,
    )
    assert state == "failed"
    assert "abc12345xyz" not in detail and "[REDACTED]" in detail


def test_retired_channels_never_change_and_old_reports_are_ignored(
    run: Any, db: PgDatabase
) -> None:
    retired = new_shop(db, state="retired")
    live = new_shop(db)

    async def scenario(env: Env) -> list[Any]:
        later = env.clock.now() + timedelta(minutes=1)
        async with env.db.tenant(retired.tenant) as conn:
            a = await channels.record_state(
                conn, retired.tenant, retired.channel_id, "connected", at=later, source="poll"
            )
        async with env.db.tenant(live.tenant) as conn:
            b = await channels.record_state(
                conn, live.tenant, live.channel_id, "needs_qr", at=later, source="poll"
            )
            c = await channels.record_state(
                conn, live.tenant, live.channel_id, "connected", at=env.clock.now(), source="poll"
            )
            d = await channels.record_state(
                conn, live.tenant, live.channel_id, "needs_qr", at=later, source="poll"
            )
        return [a, b is not None, c, d]

    assert run(scenario) == [None, True, None, None]


# Global pause -----------------------------------------------------------------------------


def test_global_pause_blocks_new_orders_for_every_tenant(
    run: Any, db: PgDatabase, clock: FakeClock
) -> None:
    shops = [new_live_shop(db), new_live_shop(db)]

    async def gates(env: Env) -> list[tuple[bool, str | None]]:
        return [await gate_of(env, s) for s in shops]

    with db.platform() as conn:
        founder = Onboarding(conn, operator="test-founder", clock=clock)
        assert run(gates) == [(True, None), (True, None)]
        founder.set_global_pause(paused=True, reason="OpenWA upgrade")
        try:
            assert founder.global_pause_status()[:2] == (True, "OpenWA upgrade")
            assert run(gates) == [(False, "global_pause"), (False, "global_pause")]
        finally:
            founder.set_global_pause(paused=False)
        assert run(gates) == [(True, None), (True, None)]
        assert founder.global_pause_status()[0] is False
        with pytest.raises(OnboardingError, match="reason"):
            founder.set_global_pause(paused=True, reason=" ")


def test_only_the_founder_can_set_the_global_pause_and_history_is_kept(db: PgDatabase) -> None:
    with db.app() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(
            "INSERT INTO velora.global_pause_events (paused, reason, operator) "
            "VALUES (true, 'x', 'app')"
        )
    with db.platform() as conn:
        for statement in (
            "UPDATE velora.global_pause_events SET reason = 'edited'",
            "DELETE FROM velora.global_pause_events",
        ):
            with pytest.raises(psycopg.Error), conn.transaction():
                conn.execute(statement)
    # Not even the administrator can rewrite the history (trigger).
    with db.admin() as conn, pytest.raises(errors.RestrictViolation):
        conn.execute("DELETE FROM velora.global_pause_events")
