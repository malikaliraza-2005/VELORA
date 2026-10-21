"""Channel state machine, circuit breaker and global pause (P5-T5, AD-19).

A channel's state comes from three sources: the provider's session events (ingress), the
health poll (every 60 seconds, `ChannelMonitor.poll`) and send errors (the outbox). Each
recorded change is appended to `channel_events`.

- **Circuit breaker:** a tenant takes new orders only while it has a `connected` channel.
  `check_new_cart` reads the state on every call, with no cache, so a recorded `needs_qr`
  stops the very next cart.
- **Global pause:** the newest `global_pause_events` row. When paused, no tenant takes new
  orders. The founder sets it with `python -m velora.admin global-pause`.

The last observation wins, except that a report older than the current state is ignored and
a retired channel never changes. Recovery is automatic: once the provider reports
`connected`, orders reopen.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from velora.clock import Clock
from velora.db import Database, TenantId
from velora.messaging.provider import ObservedState, WhatsAppProvider
from velora.redaction import error_text

log = logging.getLogger(__name__)

HEALTH_POLL_INTERVAL = timedelta(seconds=60)

Source = Literal["webhook", "poll", "send_error"]


@dataclass(frozen=True)
class Channel:
    id: uuid.UUID
    provider: str
    session_ref: str
    state: str


@dataclass(frozen=True)
class GlobalPause:
    reason: str | None
    since: datetime


@dataclass(frozen=True)
class StateChange:
    channel_id: uuid.UUID
    old: str
    new: str


async def global_pause(conn: AsyncConnection[Any]) -> GlobalPause | None:
    cur = await conn.execute(
        "SELECT paused, reason, created_at FROM velora.global_pause_events "
        "ORDER BY seq DESC LIMIT 1"
    )
    row = await cur.fetchone()
    if row is None or not row[0]:
        return None
    return GlobalPause(row[1], row[2])


async def has_connected_channel(conn: AsyncConnection[Any]) -> bool:
    cur = await conn.execute("SELECT 1 FROM velora.channels WHERE state = 'connected' LIMIT 1")
    return await cur.fetchone() is not None


async def list_channels(conn: AsyncConnection[Any]) -> list[Channel]:
    cur = await conn.execute(
        "SELECT id, provider, session_ref, state FROM velora.channels "
        "WHERE state <> 'retired' ORDER BY created_at, id"
    )
    return [Channel(*row) for row in await cur.fetchall()]


async def record_state(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    channel_id: uuid.UUID,
    state: ObservedState,
    *,
    at: datetime,
    source: Source,
    detail: str | None = None,
) -> StateChange | None:
    """Record an observed state. Returns the change, or None if nothing changed."""
    cur = await conn.execute(
        "SELECT state, state_changed_at FROM velora.channels WHERE id = %s FOR UPDATE",
        (channel_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    old, changed_at = row
    if old == "retired" or old == state or (changed_at is not None and at < changed_at):
        return None
    await conn.execute(
        "UPDATE velora.channels SET state = %s, state_changed_at = %s, updated_at = %s "
        "WHERE id = %s",
        (state, at, at, channel_id),
    )
    await conn.execute(
        "INSERT INTO velora.channel_events (tenant_id, channel_id, event_type, details, "
        "created_at) VALUES (%s, %s, 'state_changed', %s, %s)",
        (
            tenant.value,
            channel_id,
            Jsonb({"from": old, "to": state, "source": source, "detail": detail}),
            at,
        ),
    )
    log.info("channel %s: %s -> %s (%s)", channel_id, old, state, source)
    return StateChange(channel_id, old, state)


class ChannelMonitor:
    """The health poll: asks each channel's provider for its session status."""

    def __init__(
        self, db: Database, providers: Mapping[str, WhatsAppProvider], clock: Clock
    ) -> None:
        self._db = db
        self._providers = providers
        self._clock = clock

    async def poll(self, tenant: TenantId) -> list[StateChange]:
        async with self._db.tenant(tenant) as conn:
            channels = await list_channels(conn)
        changes: list[StateChange] = []
        for channel in channels:
            provider = self._providers.get(channel.provider)
            if provider is None:
                continue
            detail = None
            state: ObservedState
            try:
                state = await provider.get_session_status(channel.session_ref)
            except Exception as exc:
                # The provider itself can't be reached, so the channel can't be relied on.
                state, detail = "failed", error_text(exc)
            async with self._db.tenant(tenant) as conn:
                change = await record_state(
                    conn,
                    tenant,
                    channel.id,
                    state,
                    at=self._clock.now(),
                    source="poll",
                    detail=detail,
                )
            if change is not None:
                changes.append(change)
        return changes
