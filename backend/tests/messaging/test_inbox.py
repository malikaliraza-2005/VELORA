"""P5-T3 (provider-neutral core): ingress, deduplication, filtering and leased processing.

Exit criteria covered here:
- **Replays:** a duplicate webhook doesn't create a second event.
- **Crash test:** killing a worker in the middle of processing means the event is processed
  exactly once after its lease expires. The worker is a real process, killed with SIGKILL /
  TerminateProcess while its handler's transaction is open.
- **Filtering:** a group or status event leaves no row.
- **Signatures** (core part): a webhook that fails verification stores nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from psycopg import AsyncConnection

from tests.db.conftest import APP_ROLE, PgDatabase
from tests.messaging.conftest import T0, Env, Shop, new_shop, rows
from velora.clock import FakeClock
from velora.db import TenantId
from velora.messaging.inbox import (
    BACKOFF,
    LEASE,
    InboxEvent,
    InboxProcessor,
    Ingress,
    LeaseLostError,
    SessionMismatchError,
    UnknownChannelError,
)
from velora.messaging.provider import (
    ChatKind,
    InboundMessage,
    InvalidWebhookError,
    SessionState,
)
from velora.messaging.providers.fake import SIGNATURE_HEADER


def message(
    shop: Shop, key: str, *, sender: str = "+923000000101", chat_kind: ChatKind = "direct"
) -> InboundMessage:
    return InboundMessage(
        session_ref=shop.session_ref,
        dedup_key=key,
        provider_message_id=key,
        sender=sender,
        chat_kind=chat_kind,
        kind="text",
        text=f"text of {key}",
        received_at=T0,
    )


async def deliver(env: Env, shop: Shop, *events: Any) -> Any:
    """The webhook path: verify the raw body, then store (what the endpoint will do)."""
    headers, body = env.fake.signed_webhook(shop.session_ref, list(events))
    parsed = env.fake.parse_webhook(shop.session_ref, headers, body)
    return await Ingress(env.db, env.clock).receive("fake", shop.session_ref, parsed)


def inbox_rows(db: PgDatabase, shop: Shop) -> list[tuple[Any, ...]]:
    return rows(
        db,
        "SELECT provider_event_id, event_type, status, attempts FROM velora.inbox_events "
        "WHERE tenant_id = %s ORDER BY seq",
        shop.tenant.value,
    )


class Recorder:
    """A handler that records what it processed, in a table the test can read."""

    def __init__(self, fail_times: int = 0) -> None:
        self.seen: list[str] = []
        self.fail_times = fail_times

    async def __call__(self, conn: AsyncConnection[Any], tenant: TenantId, e: InboxEvent) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("handler failed, api_key=sk-test-should-not-be-stored")
        self.seen.append(e.payload["provider_message_id"])
        await conn.execute(
            "INSERT INTO velora.audit_log (tenant_id, actor_kind, action, target_id) "
            "VALUES (%s, 'system', 'test.processed', %s)",
            (tenant.value, e.id),
        )


# Ingress ----------------------------------------------------------------------------------


def test_duplicate_webhook_does_not_create_a_second_event(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> tuple[Any, Any]:
        first = await deliver(env, shop, message(shop, "wamid-1"))
        again = await deliver(env, shop, message(shop, "wamid-1"), message(shop, "wamid-2"))
        return first, again

    first, again = run(scenario)
    assert (first.stored, first.duplicates) == (1, 0)
    assert (again.stored, again.duplicates) == (1, 1)
    assert [r[0] for r in inbox_rows(db, shop)] == ["wamid-1", "wamid-2"]


@pytest.mark.parametrize("chat_kind", ["group", "status", "broadcast"])
def test_group_status_and_broadcast_events_leave_no_row(
    run: Any, db: PgDatabase, shop: Shop, chat_kind: ChatKind
) -> None:
    result = run(lambda env: deliver(env, shop, message(shop, "g-1", chat_kind=chat_kind)))
    assert (result.stored, result.ignored) == (0, 1)
    assert inbox_rows(db, shop) == []


def test_a_bad_signature_stores_nothing(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        _, body = env.fake.signed_webhook(shop.session_ref, [message(shop, "wamid-9")])
        with pytest.raises(InvalidWebhookError):
            env.fake.parse_webhook(shop.session_ref, {SIGNATURE_HEADER: "sha256=00"}, body)

    run(scenario)
    assert inbox_rows(db, shop) == []


def test_an_event_for_another_session_is_refused(run: Any, db: PgDatabase, shop: Shop) -> None:
    other = new_shop(db)

    async def scenario(env: Env) -> None:
        with pytest.raises(SessionMismatchError):
            await Ingress(env.db, env.clock).receive(
                "fake", shop.session_ref, [message(shop, "a"), message(other, "b")]
            )

    run(scenario)
    assert inbox_rows(db, shop) == [] and inbox_rows(db, other) == []


def test_an_unknown_or_retired_session_is_refused(run: Any, db: PgDatabase) -> None:
    retired = new_shop(db, state="retired")

    async def scenario(env: Env) -> None:
        ingress = Ingress(env.db, env.clock)
        for session in ("session-nobody", retired.session_ref):
            with pytest.raises(UnknownChannelError):
                await ingress.receive("fake", session, [])

    run(scenario)


def test_events_are_committed_before_receive_returns(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> list[tuple[Any, ...]]:
        await deliver(env, shop, message(shop, "wamid-1"))
        # Read on a separate connection while the pool is still open.
        return inbox_rows(db, shop)

    assert run(scenario) == [("wamid-1", "message", "pending", 0)]


def test_session_events_update_the_channel_state(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        late = T0 + timedelta(minutes=5)
        await deliver(env, shop, SessionState(shop.session_ref, "needs_qr", late))
        # An older report arriving later is ignored.
        await deliver(env, shop, SessionState(shop.session_ref, "connected", T0))

    run(scenario)
    assert rows(db, "SELECT state FROM velora.channels WHERE id = %s", shop.channel_id) == [
        ("needs_qr",)
    ]
    events = rows(
        db,
        "SELECT event_type, details->>'to', details->>'source' FROM velora.channel_events "
        "WHERE channel_id = %s",
        shop.channel_id,
    )
    assert events == [("state_changed", "needs_qr", "webhook")]


# Processing -------------------------------------------------------------------------------


def test_events_are_processed_once_and_in_order_per_customer(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    recorder = Recorder()

    async def scenario(env: Env) -> list[str | None]:
        await deliver(
            env,
            shop,
            message(shop, "a1", sender="+923000000101"),
            message(shop, "a2", sender="+923000000101"),
            message(shop, "b1", sender="+923000000102"),
        )
        processor = InboxProcessor(env.db, env.clock, {"message": recorder})
        # While a1 is being processed, a2 (same customer) waits; b1 doesn't.
        first = await processor.claim(shop.tenant)
        second = await processor.claim(shop.tenant)
        third = await processor.claim(shop.tenant)
        claimed = [e.payload["provider_message_id"] if e else None for e in (first, second, third)]
        assert first and second
        await processor.complete(shop.tenant, first)
        await processor.complete(shop.tenant, second)
        assert await processor.run_once(shop.tenant) == 1
        assert await processor.run_once(shop.tenant) == 0
        return claimed

    assert run(scenario) == ["a1", "b1", None]
    assert recorder.seen == ["a1", "b1", "a2"]
    assert {r[2] for r in inbox_rows(db, shop)} == {"done"}


def test_a_failing_event_backs_off_then_fails_without_blocking_others(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    recorder = Recorder(fail_times=100)

    async def scenario(env: Env) -> list[datetime]:
        await deliver(env, shop, message(shop, "bad", sender="+923000000101"))
        processor = InboxProcessor(env.db, env.clock, {"message": recorder})
        retry_times = []
        for delay in BACKOFF:
            assert await processor.run_once(shop.tenant) == 0
            (available_at,) = rows(
                db,
                "SELECT available_at FROM velora.inbox_events WHERE tenant_id = %s",
                shop.tenant.value,
            )[0]
            retry_times.append(available_at)
            assert available_at == env.clock.now() + delay
            clock.advance(delay)
        assert await processor.run_once(shop.tenant) == 0  # the 6th attempt fails for good
        # Another customer's message is not held up by the failed one.
        recorder.fail_times = 0
        await deliver(env, shop, message(shop, "good", sender="+923000000102"))
        assert await processor.run_once(shop.tenant) == 1
        return retry_times

    run(scenario)
    status = rows(
        db,
        "SELECT provider_event_id, status, attempts, last_error FROM velora.inbox_events "
        "WHERE tenant_id = %s ORDER BY seq",
        shop.tenant.value,
    )
    assert status[0][:3] == ("bad", "failed", 6)
    assert "sk-test" not in status[0][3] and "[REDACTED]" in status[0][3]
    assert status[1][:3] == ("good", "done", 1)


def test_events_without_a_handler_wait_stored(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> int:
        await deliver(env, shop, message(shop, "m1"))
        processor = InboxProcessor(env.db, env.clock, {"delivery_status": Recorder()})
        return await processor.run_once(shop.tenant)

    assert run(scenario) == 0
    assert inbox_rows(db, shop) == [("m1", "message", "pending", 0)]


def test_a_worker_that_lost_its_lease_cannot_complete(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    recorder = Recorder()

    async def scenario(env: Env) -> None:
        await deliver(env, shop, message(shop, "m1"))
        slow = InboxProcessor(env.db, env.clock, {"message": recorder})
        fast = InboxProcessor(env.db, env.clock, {"message": recorder})
        stale_claim = await slow.claim(shop.tenant)
        assert stale_claim is not None
        clock.advance(LEASE + timedelta(seconds=1))
        assert await fast.run_once(shop.tenant) == 1
        with pytest.raises(LeaseLostError):
            await slow.complete(shop.tenant, stale_claim)

    run(scenario)
    processed = rows(
        db,
        "SELECT count(*) FROM velora.audit_log WHERE tenant_id = %s AND action = 'test.processed'",
        shop.tenant.value,
    )
    assert processed == [(1,)]


# The crash test ---------------------------------------------------------------------------

_CRASHING_WORKER = textwrap.dedent(
    """
    import asyncio, os, sys, uuid
    from psycopg_pool import AsyncConnectionPool
    from velora.clock import SystemClock
    from velora.db import Database, TenantId
    from velora.messaging.inbox import InboxProcessor

    async def handler(conn, tenant, event):
        await conn.execute(
            "INSERT INTO velora.audit_log (tenant_id, actor_kind, action, target_id) "
            "VALUES (%s, 'system', 'test.processed', %s)", (tenant.value, event.id))
        print("IN_HANDLER", flush=True)
        await asyncio.sleep(3600)  # killed here, with the transaction still open

    async def main():
        async with AsyncConnectionPool(os.environ["CRASH_DB_URL"], open=False) as pool:
            processor = InboxProcessor(Database(pool), SystemClock(), {"message": handler})
            await processor.run_once(TenantId(uuid.UUID(os.environ["CRASH_TENANT"])))

    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(main(), loop_factory=loop)
    """
)


def test_killing_a_worker_mid_processing_processes_the_event_once_after_the_lease(
    db: PgDatabase, run: Any, clock: FakeClock
) -> None:
    shop = new_shop(db)
    # The killed worker runs on the real clock, so this test's clock starts there too.
    now = datetime.now(UTC)
    clock.advance(now - clock.now())
    run(lambda env: deliver(env, shop, message(shop, "crash-1")))

    env_vars = {
        **os.environ,
        "CRASH_DB_URL": db.cluster.url(db.name, APP_ROLE),
        "CRASH_TENANT": str(shop.tenant.value),
    }
    worker = subprocess.Popen(  # noqa: S603 (our own interpreter and script)
        [sys.executable, "-c", _CRASHING_WORKER],
        env=env_vars,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout is not None
        assert worker.stdout.readline().strip() == "IN_HANDLER"
        claimed = rows(
            db,
            "SELECT status, attempts FROM velora.inbox_events WHERE tenant_id = %s",
            shop.tenant.value,
        )
        assert claimed == [("processing", 1)]
    finally:
        worker.kill()
        worker.wait(timeout=30)

    recorder = Recorder()

    async def recover(env: Env) -> tuple[int, int]:
        processor = InboxProcessor(env.db, env.clock, {"message": recorder})
        before_expiry = await processor.run_once(shop.tenant)
        clock.advance(LEASE + timedelta(seconds=5))
        after_expiry = await processor.run_once(shop.tenant)
        assert await processor.run_once(shop.tenant) == 0
        return before_expiry, after_expiry

    assert run(recover) == (0, 1)
    assert recorder.seen == ["crash-1"]
    assert rows(
        db,
        "SELECT count(*) FROM velora.audit_log WHERE tenant_id = %s AND action = 'test.processed'",
        shop.tenant.value,
    ) == [(1,)]
    assert rows(
        db,
        "SELECT status, attempts FROM velora.inbox_events WHERE tenant_id = %s",
        shop.tenant.value,
    ) == [("done", 2)]
