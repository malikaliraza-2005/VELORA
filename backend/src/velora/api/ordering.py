"""Ordering settings, pause and status endpoints (P4). Changes need `SETTINGS_EDIT` and are
audited; the status view needs only `ORDERS_VIEW`, so the kitchen can see why orders stopped."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from velora.api.deps import ServicesDep, require
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.ordering.settings import OrderingSettings

router = APIRouter(prefix="/api")

SettingsEditor = Annotated[StaffSession, Depends(require(Permission.SETTINGS_EDIT))]
OrdersViewer = Annotated[StaffSession, Depends(require(Permission.ORDERS_VIEW))]


class SettingsOut(BaseModel):
    delivery_enabled: bool
    takeaway_enabled: bool
    delivery_fee_paisa: int
    delivery_min_order_paisa: int
    pickup_min_lead_minutes: int
    pickup_max_ahead_minutes: int
    tax_cod_bps: int | None
    tax_transfer_bps: int | None
    cod_enabled: bool
    transfer_enabled: bool
    cod_first_order_limit_paisa: int | None
    transfer_window_minutes: int
    max_orders_per_slot: int | None


class SettingsChange(BaseModel):
    """Only the fields sent are changed. Send null to clear a tax rate, the COD first-order
    limit or the capacity cap. Ranges are checked by the service."""

    model_config = ConfigDict(extra="forbid")

    delivery_enabled: bool | None = None
    takeaway_enabled: bool | None = None
    delivery_fee_paisa: int | None = None
    delivery_min_order_paisa: int | None = None
    pickup_min_lead_minutes: int | None = None
    pickup_max_ahead_minutes: int | None = None
    tax_cod_bps: int | None = None
    tax_transfer_bps: int | None = None
    cod_enabled: bool | None = None
    transfer_enabled: bool | None = None
    cod_first_order_limit_paisa: int | None = None
    transfer_window_minutes: int | None = None
    max_orders_per_slot: int | None = None


class PauseRequest(BaseModel):
    minutes: int = Field(ge=1, le=720)
    reason: str | None = Field(default=None, max_length=200)


class StatusOut(BaseModel):
    accepting_orders: bool
    reason: str | None
    reopens_at: datetime | None
    paused_until: datetime | None
    pause_reason: str | None
    slot_start: datetime
    slot_orders: int
    max_orders_per_slot: int | None


def _settings(found: OrderingSettings) -> SettingsOut:
    return SettingsOut.model_validate(found.editable())


@router.get("/settings/ordering")
async def get_settings(session: SettingsEditor, services: ServicesDep) -> SettingsOut:
    return _settings(await services.ordering.settings(session))


@router.patch("/settings/ordering")
async def update_settings(
    body: SettingsChange, session: SettingsEditor, services: ServicesDep
) -> SettingsOut:
    changes = body.model_dump(exclude_unset=True)
    return _settings(await services.ordering.update_settings(session, changes))


@router.post("/ordering/pause")
async def pause(body: PauseRequest, session: SettingsEditor, services: ServicesDep) -> StatusOut:
    await services.ordering.pause(session, minutes=body.minutes, reason=body.reason)
    return await status_view(session, services)


@router.post("/ordering/resume")
async def resume(session: SettingsEditor, services: ServicesDep) -> StatusOut:
    await services.ordering.resume(session)
    return await status_view(session, services)


@router.get("/ordering/status")
async def status_view(session: OrdersViewer, services: ServicesDep) -> StatusOut:
    found = await services.ordering.status(session)
    return StatusOut(
        accepting_orders=found.decision.open,
        reason=found.decision.reason,
        reopens_at=found.decision.reopens_at,
        paused_until=found.paused_until,
        pause_reason=found.pause_reason,
        slot_start=found.slot_start,
        slot_orders=found.slot_orders,
        max_orders_per_slot=found.max_orders_per_slot,
    )
