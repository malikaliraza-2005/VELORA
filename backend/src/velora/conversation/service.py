"""Staff side of conversations (P6-T6): the handoff inbox and Resume bot.

The dashboard inbox itself is P9; these are the service calls and endpoints it will use.
Both need `Permission.CHATS_HANDLE` (owner, manager). Resume bot is audited and takes the same
per-chat lock as the engine, so it can't interleave with a message being processed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from velora.clock import Clock
from velora.conversation import copy
from velora.conversation.engine import lock_conversation
from velora.db import Database
from velora.identity import audit
from velora.identity.errors import ForbiddenError, NotFoundError, expect_row
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.messaging.outbox import OutboundText, enqueue


@dataclass(frozen=True)
class HandedOver:
    conversation_id: uuid.UUID
    customer_phone: str | None
    customer_name: str | None
    handoff_at: datetime
    reason: str
    last_inbound_at: datetime | None


def _require(actor: StaffSession) -> None:
    if not actor.can(Permission.CHATS_HANDLE):
        raise ForbiddenError(f"your role can't do this ({Permission.CHATS_HANDLE})")


class ConversationService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def handoffs(self, actor: StaffSession) -> list[HandedOver]:
        """Chats waiting for staff, oldest first."""
        _require(actor)
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT c.id, cu.phone_e164, cu.display_name, c.handoff_at, c.handoff_reason, "
                "c.last_inbound_at FROM velora.conversations c "
                "JOIN velora.customers cu ON cu.id = c.customer_id "
                "WHERE c.state = 'handoff' ORDER BY c.handoff_at, c.id"
            )
            return [HandedOver(*row) for row in await cur.fetchall()]

    async def resume_bot(self, actor: StaffSession, conversation_id: uuid.UUID) -> bool:
        """Hand the chat back to the assistant. False if it wasn't handed over."""
        _require(actor)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT c.channel_id, cu.phone_e164 FROM velora.conversations c "
                "JOIN velora.customers cu ON cu.id = c.customer_id WHERE c.id = %s",
                (conversation_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise NotFoundError("conversation not found")
            channel_id, phone = row
            if phone is not None:
                await lock_conversation(conn, channel_id, phone)
            cur = await conn.execute(
                "SELECT c.state, c.context, c.state_version, c.handoff_reason, cu.opted_out_at "
                "FROM velora.conversations c JOIN velora.customers cu ON cu.id = c.customer_id "
                "WHERE c.id = %s FOR UPDATE OF c",
                (conversation_id,),
            )
            state, context, version, reason, opted_out_at = expect_row(await cur.fetchone())
            if state != "handoff":
                return False
            send = phone is not None and opted_out_at is None
            # The cart survives the handoff; anything half-done (checkout, a proposal) doesn't.
            kept: dict[str, Any] = {"cart": context["cart"]} if context.get("cart") else {}
            await conn.execute(
                "UPDATE velora.conversations SET state = 'idle', context = %s, "
                "state_version = state_version + 1, handoff_at = NULL, handoff_reason = NULL, "
                "last_outbound_at = CASE WHEN %s THEN %s ELSE last_outbound_at END, "
                "updated_at = %s WHERE id = %s",
                (Jsonb(kept), send, now, now, conversation_id),
            )
            if send:
                await enqueue(
                    conn,
                    actor.tenant,
                    channel_id=channel_id,
                    message=OutboundText(phone, copy.RESUMED),
                    idempotency_key=f"resume:{conversation_id}:{version}",
                    purpose="conversational",
                    at=now,
                    conversation_id=conversation_id,
                )
            await audit.record(
                conn,
                actor.tenant,
                at=now,
                actor_kind="staff",
                actor_id=actor.staff_id,
                action="conversation.bot_resumed",
                target_type="conversation",
                target_id=conversation_id,
                details={"handoff_reason": reason},
            )
        return True
