"""P5-T4 (provider-neutral core): the outbox sender.

Exit criterion covered here: **channel down**, the outbox holds messages and then flushes them
in order once reconnected. Also: stale conversational replies are replaced by one "we're back"
message; a critical message whose outcome is unknown is not re-sent; backoff; per-recipient
order; the rate cap; receipts; redacted error text.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env, Shop, rows
from velora.clock import FakeClock
from velora.messaging import channels
from velora.messaging.inbox import InboxProcessor, Ingress
from velora.messaging.outbox import (
    BACKOFF,
    SEND_LEASE,
    WE_ARE_BACK_TEXT,
    OutboundMedia,
    OutboundText,
    OutboxSender,
    Purpose,
    RateCap,
    apply_delivery_status,
    enqueue,
)
from velora.messaging.provider import (
    ChannelDownError,
    DeliveryStatus,
    MediaRef,
    PermanentSendError,
    SendTimeoutError,
    TransientSendError,
)
from velora.messaging.providers.fake import FakeProvider
from velora.ordering import gate

ALI = "+923000000201"
SARA = "+923000000202"
# A fake key, assembled so no secret scanner mistakes it for a real one.
FAKE_KEY = "sk-live-" + "abcdef123456"


async def put(
    env: Env,
    shop: Shop,
    to: str,
    text: str,
    *,
    purpose: Purpose = "status_update",
    critical: bool = False,
    key: str | None = None,
) -> uuid.UUID:
    async with env.db.tenant(shop.tenant) as conn:
        return await enqueue(
            conn,
            shop.tenant,
            channel_id=shop.channel_id,
            message=OutboundText(to, text),
            idempotency_key=key or f"k-{uuid.uuid4().hex}",
            purpose=purpose,
            critical=critical,
            at=env.clock.now(),
        )


async def set_state(env: Env, shop: Shop, state: Any) -> None:
    async with env.db.tenant(shop.tenant) as conn:
        await channels.record_state(
            conn, shop.tenant, shop.channel_id, state, at=env.clock.now(), source="poll"
        )


def statuses(db: PgDatabase, shop: Shop) -> list[tuple[Any, ...]]:
    return rows(
        db,
        "SELECT payload->>'text', status, attempts FROM velora.outbox_messages "
        "WHERE tenant_id = %s ORDER BY seq",
        shop.tenant.value,
    )


def sender(env: Env, **kwargs: Any) -> OutboxSender:
    return OutboxSender(env.db, env.providers, env.clock, **kwargs)


def test_channel_down_holds_messages_then_flushes_them_in_order(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock, fake: FakeProvider
) -> None:
    async def scenario(env: Env) -> None:
        await set_state(env, shop, "needs_qr")
        for to, text in [(ALI, "one"), (SARA, "two"), (ALI, "three"), (SARA, "four")]:
            await put(env, shop, to, text)
        held = await sender(env).run_once(shop.tenant)
        assert (held.held, held.sent) == (4, 0)
        assert env.fake.sent == []
        clock.advance(timedelta(minutes=10))
        assert (await sender(env).run_once(shop.tenant)).sent == 0  # still down
        await set_state(env, shop, "connected")
        flushed = await sender(env).run_once(shop.tenant)
        assert (flushed.flushed, flushed.sent) == (4, 4)

    run(scenario)
    assert [m.text for m in fake.sent] == ["one", "two", "three", "four"]
    # Waiting used no attempts: each message was tried exactly once.
    assert statuses(db, shop) == [(t, "sent", 1) for t in ["one", "two", "three", "four"]]


def test_a_send_that_finds_the_channel_down_waits_without_using_an_attempt(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> gate.GateDecision:
        await put(env, shop, ALI, "hello")
        env.fake.fail_next(shop.session_ref, ChannelDownError("needs_qr"))
        report = await sender(env).run_once(shop.tenant)
        assert (report.held, report.sent) == (1, 0)
        async with env.db.tenant(shop.tenant) as conn:
            return await gate.check_new_cart(conn, shop.tenant, env.clock.now())

    decision = run(scenario)
    assert statuses(db, shop) == [("hello", "waiting_for_channel", 0)]
    # The send error tripped the circuit breaker at once.
    assert rows(db, "SELECT state FROM velora.channels WHERE id = %s", shop.channel_id) == [
        ("needs_qr",)
    ]
    assert (decision.open, decision.reason) == (False, "channel_unavailable")


def test_stale_replies_are_replaced_by_one_we_are_back_message(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock, fake: FakeProvider
) -> None:
    async def scenario(env: Env) -> None:
        await set_state(env, shop, "connected")
        await set_state(env, shop, "reconnecting")
        await put(env, shop, ALI, "reply 1", purpose="conversational")
        await put(env, shop, ALI, "order #12 accepted", purpose="status_update")
        await put(env, shop, ALI, "reply 2", purpose="conversational")
        await put(env, shop, SARA, "reply 3", purpose="conversational")
        await sender(env).run_once(shop.tenant)
        clock.advance(timedelta(minutes=31))
        await set_state(env, shop, "connected")
        report = await sender(env).run_once(shop.tenant)
        assert report.stale == 3 and len(report.replacements) == 2
        # Running again doesn't add more replacements.
        assert (await sender(env).run_once(shop.tenant)).replacements == []

    run(scenario)
    assert fake.texts_to(ALI) == ["order #12 accepted", WE_ARE_BACK_TEXT]
    assert fake.texts_to(SARA) == [WE_ARE_BACK_TEXT]
    assert [s[1] for s in statuses(db, shop)][:4] == ["stale", "sent", "stale", "stale"]


def test_fresh_replies_after_an_outage_are_still_sent(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    async def scenario(env: Env) -> None:
        await set_state(env, shop, "reconnecting")
        await put(env, shop, ALI, "quick reply", purpose="conversational")
        await sender(env).run_once(shop.tenant)
        clock.advance(timedelta(minutes=29))
        await set_state(env, shop, "connected")
        await sender(env).run_once(shop.tenant)

    run(scenario)
    assert statuses(db, shop) == [("quick reply", "sent", 1)]


def test_a_critical_message_whose_outcome_is_unknown_is_not_resent(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock, fake: FakeProvider
) -> None:
    async def scenario(env: Env) -> None:
        await put(env, shop, ALI, "Pay Rs 1,050 to account ...", critical=True)
        env.fake.fail_next(shop.session_ref, SendTimeoutError("no answer in 15 s"))
        report = await sender(env).run_once(shop.tenant)
        assert report.unknown == 1
        clock.advance(timedelta(hours=1))
        assert (await sender(env).run_once(shop.tenant)).sent == 0

    run(scenario)
    assert fake.sent == []
    assert statuses(db, shop) == [("Pay Rs 1,050 to account ...", "unknown", 1)]


def test_a_timeout_is_retried_only_when_the_provider_sends_idempotently(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    async def scenario(env: Env) -> None:
        await put(env, shop, ALI, "safe to retry")
        env.fake.fail_next(shop.session_ref, SendTimeoutError("no answer"))
        assert (await sender(env).run_once(shop.tenant)).retrying == 1
        clock.advance(BACKOFF[0])
        assert (await sender(env).run_once(shop.tenant)).sent == 1

        env.fake = FakeProvider(idempotent_send=False)
        await put(env, shop, SARA, "not safe to retry")
        env.fake.fail_next(shop.session_ref, SendTimeoutError("no answer"))
        assert (await sender(env).run_once(shop.tenant)).unknown == 1

    run(scenario)
    assert statuses(db, shop) == [("safe to retry", "sent", 2), ("not safe to retry", "unknown", 1)]


def test_an_interrupted_critical_send_becomes_unknown(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    """A worker that dies mid-send leaves the message `sending`; after the lease it is
    marked unknown, not re-sent (P11 recovery mode does the same for every in-flight one)."""

    async def scenario(env: Env) -> None:
        await put(env, shop, ALI, "order confirmed", critical=True)
        await put(env, shop, SARA, "menu reply")
        s = sender(env)
        channel = (await _channels(env, shop))[0]
        assert await s._claim(shop.tenant, channel) is not None  # then the worker "dies"
        assert await s._claim(shop.tenant, channel) is not None
        clock.advance(SEND_LEASE + timedelta(seconds=1))
        await s.run_once(shop.tenant)

    run(scenario)
    # The non-critical one is retried with its idempotency key; the critical one is not.
    assert statuses(db, shop) == [("order confirmed", "unknown", 1), ("menu reply", "sent", 2)]


async def _channels(env: Env, shop: Shop) -> list[channels.Channel]:
    async with env.db.tenant(shop.tenant) as conn:
        return await channels.list_channels(conn)


def test_transient_errors_back_off_then_fail(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    async def scenario(env: Env) -> None:
        await put(env, shop, ALI, "retry me")
        env.fake.fail_next(
            shop.session_ref, *[TransientSendError("HTTP 503") for _ in range(len(BACKOFF) + 1)]
        )
        for delay in BACKOFF:
            assert (await sender(env).run_once(shop.tenant)).retrying == 1
            # Not due yet a moment before the delay ends.
            clock.advance(delay - timedelta(seconds=1))
            assert (await sender(env).run_once(shop.tenant)).retrying == 0
            clock.advance(timedelta(seconds=1))
        assert (await sender(env).run_once(shop.tenant)).failed == 1

    run(scenario)
    assert statuses(db, shop) == [("retry me", "failed", len(BACKOFF) + 1)]


def test_a_permanent_error_fails_at_once_and_its_text_is_redacted(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        await put(env, shop, ALI, "x")
        env.fake.fail_next(
            shop.session_ref,
            PermanentSendError(f"invalid recipient +923000000201, X-Api-Key: {FAKE_KEY}"),
        )
        assert (await sender(env).run_once(shop.tenant)).failed == 1

    run(scenario)
    ((error,),) = rows(
        db, "SELECT last_error FROM velora.outbox_messages WHERE tenant_id = %s", shop.tenant.value
    )
    assert FAKE_KEY not in error and "923000000201" not in error
    assert error.startswith("PermanentSendError: invalid recipient +*********201")


def test_a_later_message_never_overtakes_one_waiting_to_retry(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock, fake: FakeProvider
) -> None:
    async def scenario(env: Env) -> None:
        await put(env, shop, ALI, "first to ali")
        await put(env, shop, ALI, "second to ali")
        await put(env, shop, SARA, "to sara")
        env.fake.fail_next(shop.session_ref, TransientSendError("busy"))
        report = await sender(env).run_once(shop.tenant)
        assert (report.retrying, report.sent) == (1, 1)  # Sara isn't held up
        clock.advance(BACKOFF[0])
        assert (await sender(env).run_once(shop.tenant)).sent == 2

    run(scenario)
    assert fake.texts_to(ALI) == ["first to ali", "second to ali"]


def test_sending_is_rate_capped_per_channel(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    async def scenario(env: Env) -> list[int]:
        for i in range(5):
            await put(env, shop, f"+92300000030{i}", f"m{i}")
        s = sender(env, rate_cap=RateCap(count=2, per=timedelta(minutes=1)))
        sent = [(await s.run_once(shop.tenant)).sent]
        clock.advance(timedelta(seconds=30))
        sent.append((await s.run_once(shop.tenant)).sent)
        clock.advance(timedelta(seconds=31))
        sent.append((await s.run_once(shop.tenant)).sent)
        return sent

    assert run(scenario) == [2, 0, 2]


def test_enqueue_is_idempotent_and_supports_media(
    run: Any, db: PgDatabase, shop: Shop, fake: FakeProvider
) -> None:
    async def scenario(env: Env) -> None:
        first = await put(env, shop, ALI, "once", key="order-12-accepted")
        again = await put(env, shop, ALI, "once", key="order-12-accepted")
        assert first == again
        async with env.db.tenant(shop.tenant) as conn:
            await enqueue(
                conn,
                shop.tenant,
                channel_id=shop.channel_id,
                message=OutboundMedia(
                    ALI, MediaRef("https://f.velora.test/m.pdf", "application/pdf")
                ),
                idempotency_key="menu-pdf",
                purpose="conversational",
                at=env.clock.now(),
            )
        assert (await sender(env).run_once(shop.tenant)).sent == 2

    run(scenario)
    sent = fake.sent
    assert [m.text for m in sent] == ["once", None]
    assert sent[1].media == MediaRef("https://f.velora.test/m.pdf", "application/pdf")


def test_receipts_move_messages_forward_only(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        await put(env, shop, ALI, "receipt me")
        await sender(env).run_once(shop.tenant)
        (provider_id,) = [m.provider_message_id for m in env.fake.sent]
        ingress = Ingress(env.db, env.clock)
        now = env.clock.now()
        for status in ("read", "delivered"):  # out of order
            await ingress.receive(
                "fake",
                shop.session_ref,
                [
                    DeliveryStatus(
                        shop.session_ref, f"{provider_id}:{status}", provider_id, status, now
                    )
                ],
            )
        processor = InboxProcessor(env.db, env.clock, {"delivery_status": apply_delivery_status})
        assert await processor.run_once(shop.tenant) == 2

    run(scenario)
    assert statuses(db, shop) == [("receipt me", "read", 1)]
