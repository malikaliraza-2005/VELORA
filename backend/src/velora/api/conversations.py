"""Handoff inbox and Resume bot endpoints (P6-T6). The dashboard screens are P9. Both need
`CHATS_HANDLE`; the tenant comes from the session."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from velora.api.deps import ServicesDep, require
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


@router.get("/handoffs")
async def handoffs(session: ChatHandler, services: ServicesDep) -> list[HandoffOut]:
    found = await services.conversations.handoffs(session)
    return [HandoffOut.model_validate(h, from_attributes=True) for h in found]


@router.post("/{conversation_id}/resume")
async def resume(
    conversation_id: uuid.UUID, session: ChatHandler, services: ServicesDep
) -> ResumeOut:
    return ResumeOut(resumed=await services.conversations.resume_bot(session, conversation_id))
