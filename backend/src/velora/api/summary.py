"""The today summary (P9-T5). Needs `REPORTS_VIEW` (owner, manager)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from velora.api.deps import ServicesDep, require
from velora.conversation.pktime import PKT
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession

router = APIRouter(prefix="/api/summary")

ReportViewer = Annotated[StaffSession, Depends(require(Permission.REPORTS_VIEW))]


class TodayOut(BaseModel):
    day: date
    starts_at: datetime
    ends_at: datetime
    placed: int
    open: int
    completed: int
    cancelled: int
    expired: int
    completed_paisa: int
    completed_cod_paisa: int
    completed_transfer_paisa: int
    transfers_verified_paisa: int
    handoffs_waiting: int


@router.get("/today")
async def today(session: ReportViewer, services: ServicesDep, day: date | None = None) -> TodayOut:
    """`day` is a Pakistan date (YYYY-MM-DD); today by default."""
    chosen = day or services.clock.now().astimezone(PKT).date()
    found = await services.orders.today(session, chosen)
    return TodayOut.model_validate(found, from_attributes=True)
