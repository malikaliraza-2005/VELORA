"""Durable inbox: ingress and leased processing (P5-T3 core, AD-16, AD-20).

**Ingress** (`Ingress.receive`) takes canonical events that an adapter has already verified
(`WhatsAppProvider.parse_webhook`). It stores them and returns only after the commit, so the
webhook endpoint acknowledges only what is durable and returns non-2xx on any error. The
provider then redelivers, and the unique dedup key (V-05, from the adapter) drops the copy.
Group, status and broadcast messages are dropped before anything is written. Session events
update the channel state in the same transaction.

The OpenWA webhook endpoint that calls it is P5-T2/T3, blocked until the Go (V-01). That
endpoint reports each webhook it refuses with `Ingress.reject` (P10): a bad signature or
payload, another session's events, or an error while storing. Only the reason and the time
are kept, for the monitor's ingress-error and HMAC-failure alerts.

**Processing** (`InboxProcessor`) claims one event at a time with a lease:
1. claim: `FOR UPDATE SKIP LOCKED`, status `processing`, a new claim token, lease end; commit;
2. run the handler and mark the event `done` in **one** transaction, guarded by the token.

A worker that dies mid-way commits nothing, and its event is claimed again once the lease
ends. A worker that comes back after losing its lease can't complete the event: its handler's
writes roll back. So each event's effects commit exactly once. Events from one customer on
one channel are processed one at a time and in arrival order.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from velora.clock import Clock
from velora.db import Database, TenantId
from velora.identity.errors import expect_row
from velora.messaging import channels
from velora.messaging.provider import (
    DeliveryStatus,
    InboundMessage,
    ProviderEvent,
    SessionState,
)
from velora.redaction import error_text

log = logging.getLogger(__name__)

IGNORED_CHAT_KINDS = frozenset({"group", "status", "broadcast"})
LEASE = timedelta(minutes=2)
# Waits before the 2nd to 6th attempts; after the 6th failure the event is `failed`.
BACKOFF = (
    timedelta(seconds=10),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=15),
)


class UnknownChannelError(LookupError):
    """No live channel has this provider session. Nothing was stored."""


class SessionMismatchError(ValueError):
    """An event names a different session from the webhook it arrived on. Nothing was stored."""


class LeaseLostError(RuntimeError):
    """The lease ended and the event was claimed again; this worker's work was rolled back."""


@dataclass(frozen=True)
class IngressResult:
    stored: int = 0
    duplicates: int = 0
    ignored: int = 0
    session_changes: int = 0


@dataclass(frozen=True)
class InboxEvent:
    id: uuid.UUID
    channel_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    attempts: int
    claim_token: uuid.UUID


Handler = Callable[[AsyncConnection[Any], TenantId, InboxEvent], Awaitable[None]]


def _row_for(event: InboundMessage | DeliveryStatus) -> tuple[str, str | None, dict[str, Any]]:
    """event_type, ordering_key and the stored payload."""
    if isinstance(event, InboundMessage):
        media = event.media
        return (
            "message",
            event.sender,
            {
                "provider_message_id": event.provider_message_id,
                "sender": event.sender,
                "kind": event.kind,
                "text": event.text,
                "received_at": event.received_at.isoformat(),
                "media": None
                if media is None
                else {"provider_media_id": media.provider_media_id, "mime_type": media.mime_type},
            },
        )
    return (
        "delivery_status",
        None,
        {
            "provider_message_id": event.provider_message_id,
            "status": event.status,
            "occurred_at": event.occurred_at.isoformat(),
            "error": event.error,
        },
    )


RejectionReason = Literal["signature", "payload", "session_mismatch", "error"]


class Ingress:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def reject(self, provider: str, session_ref: str, reason: RejectionReason) -> bool:
        """Record a refused webhook for the monitor. Never raises: a failure to record must
        not change the answer the provider gets. False if nothing could be recorded (an
        unknown session, or the database is unavailable)."""
        try:
            tenant = await self._db.resolve_channel_tenant(provider, session_ref)
            if tenant is None:
                log.warning("webhook refused (%s) for an unknown %s session", reason, provider)
                return False
            async with self._db.tenant(tenant) as conn:
                await conn.execute(
                    "INSERT INTO velora.ingress_rejections (tenant_id, channel_id, reason, "
                    "created_at) SELECT tenant_id, id, %s, %s FROM velora.channels "
                    "WHERE provider = %s AND session_ref = %s AND state <> 'retired'",
                    (reason, self._clock.now(), provider, session_ref),
                )
        except Exception as exc:
            log.error("could not record a refused webhook: %s", error_text(exc))
            return False
        log.warning("webhook refused (%s) for tenant %s", reason, tenant.value)
        return True

    async def receive(
        self, provider: str, session_ref: str, events: Sequence[ProviderEvent]
    ) -> IngressResult:
        """Store verified events. Returns after the commit; raises if nothing was stored."""
        if any(e.session_ref != session_ref for e in events):
            raise SessionMismatchError("an event names another session")
        tenant = await self._db.resolve_channel_tenant(provider, session_ref)
        if tenant is None:
            raise UnknownChannelError("unknown provider session")
        stored = duplicates = ignored = session_changes = 0
        now = self._clock.now()
        async with self._db.tenant(tenant) as conn:
            cur = await conn.execute(
                "SELECT id FROM velora.channels WHERE provider = %s AND session_ref = %s "
                "AND state <> 'retired'",
                (provider, session_ref),
            )
            (channel_id,) = expect_row(await cur.fetchone())
            for event in events:
                if isinstance(event, SessionState):
                    change = await channels.record_state(
                        conn,
                        tenant,
                        channel_id,
                        event.state,
                        at=event.occurred_at,
                        source="webhook",
                        detail=event.detail,
                    )
                    session_changes += change is not None
                    continue
                if isinstance(event, InboundMessage) and event.chat_kind in IGNORED_CHAT_KINDS:
                    ignored += 1
                    continue
                event_type, ordering_key, payload = _row_for(event)
                cur = await conn.execute(
                    "INSERT INTO velora.inbox_events (tenant_id, channel_id, provider_event_id, "
                    "event_type, ordering_key, payload, available_at, received_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (tenant_id, channel_id, provider_event_id) DO NOTHING "
                    "RETURNING id",
                    (
                        tenant.value,
                        channel_id,
                        event.dedup_key,
                        event_type,
                        ordering_key,
                        Jsonb(payload),
                        now,
                        now,
                    ),
                )
                if await cur.fetchone() is None:
                    duplicates += 1
                else:
                    stored += 1
        return IngressResult(stored, duplicates, ignored, session_changes)


class InboxProcessor:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        handlers: Mapping[str, Handler],
        *,
        lease: timedelta = LEASE,
    ) -> None:
        self._db = db
        self._clock = clock
        self._handlers = dict(handlers)
        self._lease = lease

    @property
    def event_types(self) -> list[str]:
        """Only events with a handler are claimed; others wait, stored, for theirs."""
        return sorted(self._handlers)

    async def claim(self, tenant: TenantId) -> InboxEvent | None:
        now = self._clock.now()
        token = uuid.uuid4()
        async with self._db.tenant(tenant) as conn:
            cur = await conn.execute(
                """
                SELECT e.id FROM velora.inbox_events e
                 WHERE e.event_type = ANY(%(types)s)
                   AND ((e.status = 'pending' AND e.available_at <= %(now)s)
                        OR (e.status = 'processing' AND e.locked_until <= %(now)s))
                   AND (e.ordering_key IS NULL OR NOT EXISTS (
                        SELECT 1 FROM velora.inbox_events p
                         WHERE p.channel_id = e.channel_id AND p.ordering_key = e.ordering_key
                           AND p.id <> e.id
                           AND ((p.status IN ('pending', 'processing') AND p.seq < e.seq)
                                OR (p.status = 'processing' AND p.locked_until > %(now)s))))
                 ORDER BY e.seq
                 LIMIT 1
                   FOR UPDATE OF e SKIP LOCKED
                """,
                {"types": self.event_types, "now": now},
            )
            row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "UPDATE velora.inbox_events SET status = 'processing', claim_token = %s, "
                "locked_until = %s, attempts = attempts + 1 WHERE id = %s "
                "RETURNING id, channel_id, event_type, payload, attempts",
                (token, now + self._lease, row[0]),
            )
            event_id, channel_id, event_type, payload, attempts = expect_row(await cur.fetchone())
        return InboxEvent(event_id, channel_id, event_type, payload, attempts, token)

    async def complete(self, tenant: TenantId, event: InboxEvent) -> None:
        """Run the handler and mark the event done, atomically. Raises LeaseLostError if the
        event was claimed again meanwhile; the handler's writes are then rolled back."""
        handler = self._handlers[event.event_type]
        async with self._db.tenant(tenant) as conn:
            await handler(conn, tenant, event)
            cur = await conn.execute(
                "UPDATE velora.inbox_events SET status = 'done', processed_at = %s, "
                "claim_token = NULL, locked_until = NULL, last_error = NULL "
                "WHERE id = %s AND status = 'processing' AND claim_token = %s",
                (self._clock.now(), event.id, event.claim_token),
            )
            if cur.rowcount != 1:
                raise LeaseLostError(f"lease on inbox event {event.id} was lost")

    async def _release(self, tenant: TenantId, event: InboxEvent, exc: Exception) -> None:
        now = self._clock.now()
        retry = event.attempts <= len(BACKOFF)
        async with self._db.tenant(tenant) as conn:
            await conn.execute(
                "UPDATE velora.inbox_events SET status = %s, available_at = %s, "
                "claim_token = NULL, locked_until = NULL, last_error = %s "
                "WHERE id = %s AND status = 'processing' AND claim_token = %s",
                (
                    "pending" if retry else "failed",
                    now + BACKOFF[event.attempts - 1] if retry else now,
                    error_text(exc),
                    event.id,
                    event.claim_token,
                ),
            )

    async def run_once(self, tenant: TenantId, *, limit: int = 50) -> int:
        """Process up to `limit` due events for one tenant. Returns how many completed."""
        done = 0
        for _ in range(limit):
            event = await self.claim(tenant)
            if event is None:
                break
            try:
                await self.complete(tenant, event)
            except LeaseLostError:
                log.warning("inbox event %s: lease lost; left to the new owner", event.id)
            except Exception as exc:
                log.warning("inbox event %s failed: %s", event.id, error_text(exc))
                await self._release(tenant, event, exc)
            else:
                done += 1
        return done
