"""Order endpoints (P7-T3, P9). Viewing needs `ORDERS_VIEW`, moving an order `ORDERS_UPDATE`;
the tenant comes from the session. Every move is audited. `GET /api/orders/late-payments`
lists expired transfer orders for the late review (P8-T5), which the active list leaves out."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from velora.api.deps import ServicesDep, require
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.orders.lifecycle import REASON_MAX, Status

router = APIRouter(prefix="/api/orders")

OrdersViewer = Annotated[StaffSession, Depends(require(Permission.ORDERS_VIEW))]
OrdersUpdater = Annotated[StaffSession, Depends(require(Permission.ORDERS_UPDATE))]


class OrderLineOut(BaseModel):
    line_no: int
    sku: str
    name: str
    choices: list[list[str]]
    unit_price_paisa: int
    quantity: int
    line_total_paisa: int


class OrderMoveOut(BaseModel):
    from_status: str | None
    to_status: str
    actor_kind: str
    actor_staff_id: uuid.UUID | None
    reason: str | None
    at: datetime


class OrderOut(BaseModel):
    id: uuid.UUID
    reference: str
    status: str
    fulfilment: str
    payment_method: str | None
    subtotal_paisa: int
    delivery_fee_paisa: int
    tax_paisa: int
    total_paisa: int
    delivery_address: str | None
    delivery_note: str | None
    pickup_at: datetime | None
    payment_due_at: datetime | None
    possible_duplicate: bool
    customer_phone: str | None
    created_at: datetime
    updated_at: datetime
    placed_during_recovery: bool
    next_statuses: list[str]
    lines: list[OrderLineOut]
    history: list[OrderMoveOut]


class MoveRequest(BaseModel):
    """`from` is the status the staff member saw; a move from anything else is refused."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_status: Status = Field(alias="from")
    to: Status
    reason: str | None = Field(default=None, max_length=REASON_MAX)


@router.get("")
async def list_orders(
    session: OrdersViewer, services: ServicesDep, active: bool = True
) -> list[OrderOut]:
    found = await services.orders.list_orders(session, active=active)
    return [OrderOut.model_validate(o, from_attributes=True) for o in found]


class LatePaymentOut(BaseModel):
    order: OrderOut
    evidence_count: int


@router.get("/late-payments")
async def late_payments(session: OrdersViewer, services: ServicesDep) -> list[LatePaymentOut]:
    found = await services.orders.late_payments(session)
    return [LatePaymentOut.model_validate(f, from_attributes=True) for f in found]


@router.get("/{order_id}")
async def get_order(order_id: uuid.UUID, session: OrdersViewer, services: ServicesDep) -> OrderOut:
    found = await services.orders.get(session, order_id)
    return OrderOut.model_validate(found, from_attributes=True)


@router.post("/{order_id}/status")
async def move_order(
    order_id: uuid.UUID, body: MoveRequest, session: OrdersUpdater, services: ServicesDep
) -> OrderOut:
    moved = await services.orders.move(
        session, order_id, to=body.to, expected=body.from_status, reason=body.reason
    )
    return OrderOut.model_validate(moved, from_attributes=True)
