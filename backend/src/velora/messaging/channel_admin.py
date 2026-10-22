"""The owner's WhatsApp controls (P9-T6): channel status and QR re-link.

All need `Permission.CHANNELS_MANAGE`, which only the owner holds: a pairing QR code gives
control of the restaurant's ordering number. Other staff see only whether orders are being
taken, and why not (`channel_unavailable`, `/api/ordering/status`).

- **Re-link** asks the provider to start a pairing session and records the state it reports.
  It is audited. Once the phone has scanned the code, the provider reports `connected`
  (session webhook, or the health poll) and orders reopen (P5 circuit breaker).
- **The QR code** is fetched from the provider on demand. It is never stored, logged or
  audited, and the API sends it with `Cache-Control: no-store`.

The providers are passed in by the composition root, so this module never imports an
adapter. A channel whose adapter isn't running (OpenWA until the Go, V-01) can't be re-linked.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from velora.clock import Clock
from velora.db import Database
from velora.identity import audit
from velora.identity.errors import ConflictError, ForbiddenError, NotFoundError
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.messaging import channels
from velora.messaging.provider import WhatsAppProvider
from velora.redaction import error_text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelStatus:
    id: uuid.UUID
    provider: str
    state: str
    state_changed_at: datetime | None
    # Whether this deployment runs the channel's adapter, so it can be re-linked here.
    adapter_running: bool


def _require(actor: StaffSession) -> None:
    if not actor.can(Permission.CHANNELS_MANAGE):
        raise ForbiddenError(f"your role can't do this ({Permission.CHANNELS_MANAGE})")


class ChannelService:
    def __init__(
        self, db: Database, providers: Mapping[str, WhatsAppProvider], clock: Clock
    ) -> None:
        self._db = db
        self._providers = providers
        self._clock = clock

    async def list(self, actor: StaffSession) -> list[ChannelStatus]:
        _require(actor)
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT id, provider, state, state_changed_at FROM velora.channels "
                "WHERE state <> 'retired' ORDER BY created_at, id"
            )
            rows = await cur.fetchall()
        return [
            ChannelStatus(id_, provider, state, changed_at, provider in self._providers)
            for id_, provider, state, changed_at in rows
        ]

    async def relink(self, actor: StaffSession, channel_id: uuid.UUID) -> ChannelStatus:
        """Start pairing, so the owner can scan a new QR code."""
        _require(actor)
        async with self._db.tenant(actor.tenant) as conn:
            channel = await self._channel(conn, channel_id)
        if channel.state == "connected":
            raise ConflictError("WhatsApp is already connected")
        provider = self._provider(channel)
        try:
            await provider.start_session(channel.session_ref)
            observed = await provider.get_session_status(channel.session_ref)
        except Exception as exc:
            log.warning("re-link of channel %s failed: %s", channel.id, error_text(exc))
            raise ConflictError("the WhatsApp gateway didn't respond; try again") from exc
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            await channels.record_state(
                conn, actor.tenant, channel.id, observed, at=now, source="relink"
            )
            await audit.record(
                conn,
                actor.tenant,
                at=now,
                actor_kind="staff",
                actor_id=actor.staff_id,
                action="channel.relink_started",
                target_type="channel",
                target_id=channel.id,
                details={"from": channel.state, "observed": observed},
            )
        found = [c for c in await self.list(actor) if c.id == channel.id]
        if not found:
            raise NotFoundError("channel not found")
        return found[0]

    async def qr(self, actor: StaffSession, channel_id: uuid.UUID) -> str | None:
        """The pairing QR code while the provider has one, otherwise None."""
        _require(actor)
        async with self._db.tenant(actor.tenant) as conn:
            channel = await self._channel(conn, channel_id)
        provider = self._provider(channel)
        try:
            return await provider.get_qr(channel.session_ref)
        except Exception as exc:
            log.warning("QR fetch for channel %s failed: %s", channel.id, error_text(exc))
            raise ConflictError("the WhatsApp gateway didn't respond; try again") from exc

    async def _channel(self, conn: AsyncConnection[Any], channel_id: uuid.UUID) -> channels.Channel:
        cur = await conn.execute(
            "SELECT id, provider, session_ref, state FROM velora.channels "
            "WHERE id = %s AND state <> 'retired'",
            (channel_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise NotFoundError("channel not found")
        return channels.Channel(*row)

    def _provider(self, channel: channels.Channel) -> WhatsAppProvider:
        provider = self._providers.get(channel.provider)
        if provider is None:
            raise ConflictError("this WhatsApp connection can't be re-linked from here yet")
        return provider
