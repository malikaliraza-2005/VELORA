"""Staff side of conversations (P6-T6, P9-T4): the handoff inbox, the chat history, staff
replies and Resume bot.

All need `Permission.CHATS_HANDLE` (owner, manager). Replies and Resume bot are audited and
take the same per-chat lock as the engine, so they can't interleave with a message being
processed.

- **Replies** go through the outbox like every other message (AD-16), only while the chat is
  handed over and the customer hasn't opted out. The audit row records the length, not the
  text.
- **Message problems** (P5 finding): outbound messages from the last day that `failed` or
  whose outcome is `unknown`, so staff can follow up by hand. Nothing is re-sent
  automatically.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from velora.clock import Clock
from velora.conversation import copy
from velora.conversation.engine import lock_conversation
from velora.db import Database
from velora.identity import audit
from velora.identity.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    expect_row,
)
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.messaging.outbox import OutboundText, enqueue

REPLY_MAX = 1000
HISTORY_LIMIT = 50
PROBLEMS_WINDOW = timedelta(hours=24)
PROBLEMS_LIMIT = 100


@dataclass(frozen=True)
class HandedOver:
    conversation_id: uuid.UUID
    customer_phone: str | None
    customer_name: str | None
    handoff_at: datetime
    reason: str
    last_inbound_at: datetime | None


@dataclass(frozen=True)
class ChatMessage:
    direction: Literal["in", "out"]
    kind: str
    text: str | None
    at: datetime
    # Outbound only: the outbox status (pending, sent, delivered, failed, unknown, ...).
    status: str | None


@dataclass(frozen=True)
class MessageProblem:
    message_id: uuid.UUID
    status: str
    to: str
    text: str | None
    purpose: str
    created_at: datetime
    last_error: str | None
    conversation_id: uuid.UUID | None


def _require(actor: StaffSession) -> None:
    if not actor.can(Permission.CHATS_HANDLE):
        raise ForbiddenError(f"your role can't do this ({Permission.CHATS_HANDLE})")


async def _chat(conn: AsyncConnection[Any], conversation_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    """The chat's channel and the customer's number."""
    cur = await conn.execute(
        "SELECT c.channel_id, cu.phone_e164 FROM velora.conversations c "
        "JOIN velora.customers cu ON cu.id = c.customer_id WHERE c.id = %s",
        (conversation_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError("conversation not found")
    if row[1] is None:
        raise ConflictError("this customer has no WhatsApp number on record")
    return row[0], row[1]


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

    async def messages(self, actor: StaffSession, conversation_id: uuid.UUID) -> list[ChatMessage]:
        """The chat's latest messages, both ways, oldest first."""
        _require(actor)
        async with self._db.tenant(actor.tenant) as conn:
            channel_id, phone = await _chat(conn, conversation_id)
            cur = await conn.execute(
                "SELECT * FROM ("
                "SELECT 'in' AS direction, payload->>'kind' AS kind, payload->>'text' AS text, "
                "received_at AS at, NULL AS status FROM velora.inbox_events "
                "WHERE channel_id = %(ch)s AND event_type = 'message' AND ordering_key = %(to)s "
                "UNION ALL "
                "SELECT 'out', kind, COALESCE(payload->>'text', payload->>'caption'), created_at, "
                "status FROM velora.outbox_messages "
                "WHERE channel_id = %(ch)s AND ordering_key = %(to)s "
                "ORDER BY at DESC LIMIT %(limit)s) latest ORDER BY at",
                {"ch": channel_id, "to": phone, "limit": HISTORY_LIMIT},
            )
            return [ChatMessage(*row) for row in await cur.fetchall()]

    async def reply(
        self, actor: StaffSession, conversation_id: uuid.UUID, text: str, *, request_key: str
    ) -> uuid.UUID:
        """Queue a staff reply to a handed-over chat. Returns the outbox message id; the same
        `request_key` from the same session queues it once."""
        _require(actor)
        text = text.strip()
        if not 1 <= len(text) <= REPLY_MAX:
            raise InvalidInputError(f"a reply must be 1 to {REPLY_MAX} characters")
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            channel_id, phone = await _chat(conn, conversation_id)
            await lock_conversation(conn, channel_id, phone)
            cur = await conn.execute(
                "SELECT c.state, cu.opted_out_at FROM velora.conversations c "
                "JOIN velora.customers cu ON cu.id = c.customer_id WHERE c.id = %s "
                "FOR UPDATE OF c",
                (conversation_id,),
            )
            state, opted_out_at = expect_row(await cur.fetchone())
            if state != "handoff":
                raise ConflictError("the assistant is handling this chat; it isn't handed over")
            if opted_out_at is not None:
                raise ConflictError("this customer has opted out of messages")
            message_id = await enqueue(
                conn,
                actor.tenant,
                channel_id=channel_id,
                message=OutboundText(phone, text),
                idempotency_key=f"staff-reply:{actor.session_id}:{request_key}",
                purpose="conversational",
                at=now,
                conversation_id=conversation_id,
            )
            await conn.execute(
                "UPDATE velora.conversations SET last_outbound_at = %s, updated_at = %s "
                "WHERE id = %s",
                (now, now, conversation_id),
            )
            await audit.record(
                conn,
                actor.tenant,
                at=now,
                actor_kind="staff",
                actor_id=actor.staff_id,
                action="conversation.staff_replied",
                target_type="conversation",
                target_id=conversation_id,
                details={"message_id": str(message_id), "length": len(text)},
            )
        return message_id

    async def message_problems(self, actor: StaffSession) -> list[MessageProblem]:
        """Outbound messages from the last day that failed or whose outcome is unknown."""
        _require(actor)
        since = self._clock.now() - PROBLEMS_WINDOW
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT id, status, ordering_key, COALESCE(payload->>'text', "
                "payload->>'caption'), purpose, created_at, last_error, conversation_id "
                "FROM velora.outbox_messages WHERE status IN ('failed', 'unknown') "
                "AND created_at >= %s ORDER BY created_at DESC LIMIT %s",
                (since, PROBLEMS_LIMIT),
            )
            return [MessageProblem(*row) for row in await cur.fetchall()]
