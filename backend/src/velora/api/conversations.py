"""Chats endpoints (P6-T6, P9-T4): the handoff inbox, a chat's history, staff replies, Resume
bot and the outbound messages that need a look. All need `CHATS_HANDLE`; the tenant comes
from the session."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field

from velora.api.deps import ServicesDep, require
from velora.api.idempotency import HEADER
from velora.conversation.service import REPLY_MAX
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession

router = APIRouter(prefix="/api/conversations")

ChatHandler = Annotated[StaffSession, Depends(require(Permission.CHATS_HANDLE))]


class HandoffOut(BaseModel):
    conversation_id: uuid.UUID
    customer_phone: str | None
    customer_name: str | None
    handoff_at: datetime
    reason: str
    last_inbound_at: datetime | None


class ResumeOut(BaseModel):
    resumed: bool


class ChatMessageOut(BaseModel):
    direction: str
    kind: str
    text: str | None
    at: datetime
    status: str | None


class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=REPLY_MAX)


class ReplyOut(BaseModel):
    message_id: uuid.UUID


class MessageProblemOut(BaseModel):
    message_id: uuid.UUID
    status: str
    to: str
    text: str | None
    purpose: str
    created_at: datetime
    last_error: str | None
    conversation_id: uuid.UUID | None


@router.get("/handoffs")
async def handoffs(session: ChatHandler, services: ServicesDep) -> list[HandoffOut]:
    found = await services.conversations.handoffs(session)
    return [HandoffOut.model_validate(h, from_attributes=True) for h in found]


@router.get("/message-problems")
async def message_problems(session: ChatHandler, services: ServicesDep) -> list[MessageProblemOut]:
    found = await services.conversations.message_problems(session)
    return [MessageProblemOut.model_validate(m, from_attributes=True) for m in found]


@router.get("/{conversation_id}/messages")
async def messages(
    conversation_id: uuid.UUID, session: ChatHandler, services: ServicesDep
) -> list[ChatMessageOut]:
    found = await services.conversations.messages(session, conversation_id)
    return [ChatMessageOut.model_validate(m, from_attributes=True) for m in found]


@router.post("/{conversation_id}/reply", status_code=status.HTTP_201_CREATED)
async def reply(
    conversation_id: uuid.UUID,
    body: ReplyRequest,
    session: ChatHandler,
    services: ServicesDep,
    request_key: Annotated[str, Header(alias=HEADER)],
) -> ReplyOut:
    message_id = await services.conversations.reply(
        session, conversation_id, body.text, request_key=request_key
    )
    return ReplyOut(message_id=message_id)


@router.post("/{conversation_id}/resume")
async def resume(
    conversation_id: uuid.UUID, session: ChatHandler, services: ServicesDep
) -> ResumeOut:
    return ResumeOut(resumed=await services.conversations.resume_bot(session, conversation_id))
