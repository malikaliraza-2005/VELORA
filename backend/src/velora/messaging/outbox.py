"""Durable outbox and sender (P5-T4 core, AD-16, AD-20).

`enqueue` writes a message inside the caller's transaction, so a state change and the message
announcing it commit together. Each message has an idempotency key; enqueueing the same key
twice returns the first message.

`OutboxSender.run_once` sends one tenant's due messages:
- **States:** pending → sending → sent → delivered → read, or failed / unknown. Delivery and
  read receipts arrive through the inbox (`apply_delivery_status`).
- **Channel down:** pending messages wait in `waiting_for_channel` and use no attempts. Once
  the channel is `connected` they are flushed in their original order.
- **Order:** messages to one recipient on one channel go out one at a time, in order. A later
  message never overtakes an earlier one that is waiting for a retry.
- **Staleness:** a conversational reply not sent within 30 minutes is dropped (`stale`), and
  each affected recipient gets one "we're back" message instead. Status updates never go
  stale.
- **Retries:** backoff of 10 s, 30 s, 2 m, 5 m and 15 m, then `failed`.
- **Unknown outcomes (V-06):** after a timeout, or a lease that ended mid-send, a message is
  retried only if it is not critical and the provider guarantees idempotent sends. Otherwise
  it is marked `unknown` for staff, and never re-sent automatically.
- **Rate cap:** at most `RateCap.count` sends per channel per `RateCap.per`. This is a safety
  limit only; there is no human-like pacing or other detection evasion (AD-24).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from velora.clock import Clock
from velora.db import Database, TenantId
from velora.identity.errors import expect_row
from velora.messaging import channels
from velora.messaging.inbox import InboxEvent
from velora.messaging.provider import (
    ChannelDownError,
    MediaRef,
    PermanentSendError,
    SendResult,
    SendTimeoutError,
    TransientSendError,
    WhatsAppProvider,
)
from velora.redaction import error_text

log = logging.getLogger(__name__)

Purpose = Literal["conversational", "status_update"]

STALE_AFTER = timedelta(minutes=30)
SEND_LEASE = timedelta(minutes=2)
BACKOFF = (
    timedelta(seconds=10),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=15),
)
# Customer wording (P6, MVP scope Appendix A13; draft, untested with customers).
WE_ARE_BACK_TEXT = (
    "Sorry for the delay — we had a short technical problem and we're back now. "
    "If you still need help, please send your message again."
)


@dataclass(frozen=True)
class RateCap:
    count: int = 20
    per: timedelta = timedelta(minutes=1)


@dataclass(frozen=True)
class OutboundText:
    to: str
    text: str


@dataclass(frozen=True)
class OutboundMedia:
    to: str
    media: MediaRef
    caption: str | None = None


@dataclass
class SendReport:
    sent: int = 0
    retrying: int = 0
    failed: int = 0
    unknown: int = 0
    held: int = 0
    stale: int = 0
    flushed: int = 0
    replacements: list[uuid.UUID] = field(default_factory=list)


async def enqueue(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    *,
    channel_id: uuid.UUID,
    message: OutboundText | OutboundMedia,
    idempotency_key: str,
    purpose: Purpose,
    at: datetime,
    critical: bool = False,
    conversation_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Queue a message in the caller's transaction. Returns its id (the existing one if this
    idempotency key was already used)."""
    payload: dict[str, Any]
    if isinstance(message, OutboundText):
        kind, payload = "text", {"to": message.to, "text": message.text}
    else:
        kind = "media"
        payload = {
            "to": message.to,
            "caption": message.caption,
            "media": {
                "url": message.media.url,
                "mime_type": message.media.mime_type,
                "filename": message.media.filename,
            },
        }
    cur = await conn.execute(
        "INSERT INTO velora.outbox_messages (tenant_id, channel_id, conversation_id, "
        "idempotency_key, kind, payload, ordering_key, purpose, critical, stale_after, "
        "next_attempt_at, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id",
        (
            tenant.value,
            channel_id,
            conversation_id,
            idempotency_key,
            kind,
            Jsonb(payload),
            message.to,
            purpose,
            critical,
            at + STALE_AFTER if purpose == "conversational" else None,
            at,
            at,
        ),
    )
    row = await cur.fetchone()
    if row is None:
        cur = await conn.execute(
            "SELECT id FROM velora.outbox_messages WHERE idempotency_key = %s",
            (idempotency_key,),
        )
        row = expect_row(await cur.fetchone())
    message_id: uuid.UUID = row[0]
    return message_id


async def replace_stale(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    channel_id: uuid.UUID,
    now: datetime,
    *,
    already_told: Collection[str] = (),
) -> tuple[int, list[uuid.UUID]]:
    """Drop the channel's conversational replies past their staleness window, and queue one
    "we're back" message per affected recipient, except those in `already_told` (recovery
    has just sent them its own apology). Returns (dropped, replacement ids)."""
    cur = await conn.execute(
        "UPDATE velora.outbox_messages SET status = 'stale', "
        "last_error = 'not sent within the staleness window' "
        "WHERE channel_id = %s AND status IN ('pending', 'waiting_for_channel') "
        "AND purpose = 'conversational' AND stale_after <= %s "
        "RETURNING id, ordering_key, seq",
        (channel_id, now),
    )
    dropped = 0
    first_stale: dict[str, tuple[int, uuid.UUID]] = {}
    for message_id, recipient, seq in await cur.fetchall():
        dropped += 1
        if (
            recipient is not None
            and recipient not in already_told
            and (recipient not in first_stale or seq < first_stale[recipient][0])
        ):
            first_stale[recipient] = (seq, message_id)
    replacements = [
        await enqueue(
            conn,
            tenant,
            channel_id=channel_id,
            message=OutboundText(recipient, WE_ARE_BACK_TEXT),
            idempotency_key=f"we-are-back:{message_id}",
            purpose="conversational",
            at=now,
        )
        for recipient, (_, message_id) in first_stale.items()
    ]
    return dropped, replacements


# Receipts may only move a message forward.
_RECEIPT_FROM: dict[str, tuple[str, ...]] = {
    "sent": ("unknown",),
    "delivered": ("sent", "unknown"),
    "read": ("sent", "delivered", "unknown"),
    "failed": ("sent", "unknown"),
}


async def apply_delivery_status(
    conn: AsyncConnection[Any], tenant: TenantId, event: InboxEvent
) -> None:
    """Inbox handler for `delivery_status` events."""
    status = event.payload["status"]
    await conn.execute(
        "UPDATE velora.outbox_messages SET status = %s, last_error = %s "
        "WHERE channel_id = %s AND provider_message_id = %s AND status = ANY(%s)",
        (
            status,
            event.payload.get("error") if status == "failed" else None,
            event.channel_id,
            event.payload["provider_message_id"],
            list(_RECEIPT_FROM.get(status, ())),
        ),
    )


@dataclass(frozen=True)
class _Claimed:
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    critical: bool
    attempts: int
    token: uuid.UUID


class OutboxSender:
    def __init__(
        self,
        db: Database,
        providers: Mapping[str, WhatsAppProvider],
        clock: Clock,
        *,
        rate_cap: RateCap | None = None,
        lease: timedelta = SEND_LEASE,
    ) -> None:
        self._db = db
        self._providers = providers
        self._clock = clock
        self._cap = rate_cap or RateCap()
        self._lease = lease

    async def run_once(self, tenant: TenantId) -> SendReport:
        report = SendReport()
        async with self._db.tenant(tenant) as conn:
            found = await channels.list_channels(conn)
        for channel in found:
            provider = self._providers.get(channel.provider)
            if provider is None:
                continue
            budget = await self._prepare(tenant, channel, provider, report)
            while budget > 0:
                claimed = await self._claim(tenant, channel)
                if claimed is None:
                    break
                budget -= 1
                if not await self._send(tenant, channel, provider, claimed, report):
                    break
        return report

    async def _prepare(
        self,
        tenant: TenantId,
        channel: channels.Channel,
        provider: WhatsAppProvider,
        report: SendReport,
    ) -> int:
        """Recover expired leases, hold or flush, drop stale replies. Returns the send budget."""
        now = self._clock.now()
        retry_unknown = provider.capabilities().idempotent_send
        async with self._db.tenant(tenant) as conn:
            cur = await conn.execute(
                "UPDATE velora.outbox_messages SET claim_token = NULL, locked_until = NULL, "
                "status = CASE WHEN critical OR NOT %(retry)s THEN 'unknown' ELSE 'pending' END, "
                "last_error = 'the send was interrupted; outcome unknown' "
                "WHERE channel_id = %(ch)s AND status = 'sending' AND locked_until <= %(now)s",
                {"retry": retry_unknown, "ch": channel.id, "now": now},
            )
            if channel.state != "connected":
                cur = await conn.execute(
                    "UPDATE velora.outbox_messages SET status = 'waiting_for_channel' "
                    "WHERE channel_id = %s AND status = 'pending'",
                    (channel.id,),
                )
                report.held += cur.rowcount
                return 0
            stale, replacements = await replace_stale(conn, tenant, channel.id, now)
            report.stale += stale
            report.replacements.extend(replacements)
            cur = await conn.execute(
                "UPDATE velora.outbox_messages SET status = 'pending', next_attempt_at = %s "
                "WHERE channel_id = %s AND status = 'waiting_for_channel'",
                (now, channel.id),
            )
            report.flushed += cur.rowcount
            cur = await conn.execute(
                "SELECT count(*) FROM velora.outbox_messages "
                "WHERE channel_id = %s AND sent_at > %s",
                (channel.id, now - self._cap.per),
            )
            (recent,) = expect_row(await cur.fetchone())
        return max(0, self._cap.count - int(recent))

    async def _claim(self, tenant: TenantId, channel: channels.Channel) -> _Claimed | None:
        now = self._clock.now()
        token = uuid.uuid4()
        async with self._db.tenant(tenant) as conn:
            cur = await conn.execute(
                """
                SELECT m.id FROM velora.outbox_messages m
                 WHERE m.channel_id = %(ch)s AND m.status = 'pending'
                   AND m.next_attempt_at <= %(now)s
                   AND (m.ordering_key IS NULL OR NOT EXISTS (
                        SELECT 1 FROM velora.outbox_messages p
                         WHERE p.channel_id = m.channel_id AND p.ordering_key = m.ordering_key
                           AND p.seq < m.seq
                           AND p.status IN ('pending', 'waiting_for_channel', 'sending')))
                 ORDER BY m.seq
                 LIMIT 1
                   FOR UPDATE OF m SKIP LOCKED
                """,
                {"ch": channel.id, "now": now},
            )
            row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "UPDATE velora.outbox_messages SET status = 'sending', claim_token = %s, "
                "locked_until = %s, attempts = attempts + 1 WHERE id = %s "
                "RETURNING id, kind, payload, idempotency_key, critical, attempts",
                (token, now + self._lease, row[0]),
            )
            message_id, kind, payload, key, critical, attempts = expect_row(await cur.fetchone())
        return _Claimed(message_id, kind, payload, key, critical, attempts, token)

    async def _send(
        self,
        tenant: TenantId,
        channel: channels.Channel,
        provider: WhatsAppProvider,
        message: _Claimed,
        report: SendReport,
    ) -> bool:
        """Send one claimed message and record the outcome. False stops this channel's run."""
        try:
            result = await self._call(provider, channel.session_ref, message)
        except ChannelDownError as exc:
            await self._hold(tenant, channel, message, exc)
            report.held += 1
            return False
        except PermanentSendError as exc:
            await self._finish(tenant, message, "failed", error=exc)
            report.failed += 1
        except TransientSendError as exc:
            self._count(report, await self._retry_or_fail(tenant, message, exc))
        except Exception as exc:  # SendTimeoutError, or an error mid-request: outcome unknown
            if message.critical or not provider.capabilities().idempotent_send:
                await self._finish(tenant, message, "unknown", error=exc)
                report.unknown += 1
            else:
                self._count(report, await self._retry_or_fail(tenant, message, exc))
            if not isinstance(exc, SendTimeoutError):
                log.warning("outbox %s: unexpected send error: %s", message.id, error_text(exc))
        else:
            await self._finish(tenant, message, "sent", result=result)
            report.sent += 1
        return True

    @staticmethod
    async def _call(provider: WhatsAppProvider, session_ref: str, m: _Claimed) -> SendResult:
        to = m.payload["to"]
        if m.kind == "text":
            return await provider.send_text(
                session_ref, to, m.payload["text"], idempotency_key=m.idempotency_key
            )
        media = MediaRef(**m.payload["media"])
        return await provider.send_media(
            session_ref,
            to,
            media,
            caption=m.payload.get("caption"),
            idempotency_key=m.idempotency_key,
        )

    @staticmethod
    def _count(report: SendReport, status: str) -> None:
        if status == "failed":
            report.failed += 1
        else:
            report.retrying += 1

    async def _finish(
        self,
        tenant: TenantId,
        message: _Claimed,
        status: str,
        *,
        result: SendResult | None = None,
        error: Exception | None = None,
        next_attempt_at: datetime | None = None,
        give_back_attempt: bool = False,
    ) -> None:
        now = self._clock.now()
        async with self._db.tenant(tenant) as conn:
            cur = await conn.execute(
                "UPDATE velora.outbox_messages SET status = %s, claim_token = NULL, "
                "locked_until = NULL, sent_at = %s, provider_message_id = %s, last_error = %s, "
                "next_attempt_at = coalesce(%s, next_attempt_at), "
                "attempts = attempts - %s "
                "WHERE id = %s AND status = 'sending' AND claim_token = %s",
                (
                    status,
                    now if result is not None else None,
                    None if result is None else result.provider_message_id,
                    None if error is None else error_text(error),
                    next_attempt_at,
                    1 if give_back_attempt else 0,
                    message.id,
                    message.token,
                ),
            )
        if cur.rowcount != 1:
            log.warning("outbox %s: lease lost before recording %s", message.id, status)

    async def _retry_or_fail(self, tenant: TenantId, message: _Claimed, exc: Exception) -> str:
        if message.attempts > len(BACKOFF):
            await self._finish(tenant, message, "failed", error=exc)
            return "failed"
        retry_at = self._clock.now() + BACKOFF[message.attempts - 1]
        await self._finish(tenant, message, "pending", error=exc, next_attempt_at=retry_at)
        return "pending"

    async def _hold(
        self,
        tenant: TenantId,
        channel: channels.Channel,
        message: _Claimed,
        exc: ChannelDownError,
    ) -> None:
        """The channel is down: the message waits, without using an attempt."""
        await self._finish(
            tenant, message, "waiting_for_channel", error=exc, give_back_attempt=True
        )
        async with self._db.tenant(tenant) as conn:
            await channels.record_state(
                conn,
                tenant,
                channel.id,
                exc.state,
                at=self._clock.now(),
                source="send_error",
                detail=error_text(exc),
            )
