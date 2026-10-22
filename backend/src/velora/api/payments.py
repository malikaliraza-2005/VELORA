"""Payment endpoints (P8). The dashboard screens are P9. The tenant comes from the session.

- `GET /api/orders/{id}/payment`: the ledger, the customer's evidence and what staff may do
  (`ORDERS_VIEW`).
- `POST /api/orders/{id}/payment/verify` and `.../late-review`: the transaction reference and
  the confirmer's PIN (`ORDERS_UPDATE` for the session; the confirmer must hold
  `PAYMENTS_CONFIRM`). A shared kitchen device names the confirmer.
- `POST /api/payments/{id}/reverse` and `GET /api/payments/reconciliation`: owner only.
- `PUT` / `DELETE /api/customers/{id}/cod-block`: the COD blocklist.
- `GET /api/payments/confirmers`: who may confirm with a PIN, for the kitchen tablet's picker
  (`ORDERS_UPDATE`; P9-T3).

Every change is audited.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from velora.api.deps import ServicesDep, require
from velora.conversation.pktime import PKT
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.payments.service import REASON_MAX, REFERENCE_MAX, LateDecision, PaymentView

router = APIRouter(prefix="/api")

OrdersViewer = Annotated[StaffSession, Depends(require(Permission.ORDERS_VIEW))]
OrdersUpdater = Annotated[StaffSession, Depends(require(Permission.ORDERS_UPDATE))]
Reconciler = Annotated[StaffSession, Depends(require(Permission.PAYMENTS_RECONCILE))]
Reverser = Annotated[StaffSession, Depends(require(Permission.PAYMENTS_REVERSE))]
CodBlocker = Annotated[StaffSession, Depends(require(Permission.CUSTOMERS_BLOCK_COD))]


class ConfirmerOut(BaseModel):
    id: uuid.UUID
    display_name: str


class LedgerEntryOut(BaseModel):
    id: uuid.UUID
    status: str
    method: str
    amount_paisa: int
    reference: str | None
    verified_by_staff_id: uuid.UUID | None
    verified_by_name: str | None
    verified_at: datetime | None
    late: bool
    corrects_payment_id: uuid.UUID | None
    reason: str | None
    created_at: datetime


class EvidenceOut(BaseModel):
    id: uuid.UUID
    kind: str
    provider_media_id: str | None
    mime_type: str | None
    received_at: datetime


class PaymentOut(BaseModel):
    order_id: uuid.UUID
    reference: str
    status: str
    payment_method: str | None
    total_paisa: int
    payment_due_at: datetime | None
    customer_id: uuid.UUID
    cod_blocked: bool
    actions: list[str]
    ledger: list[LedgerEntryOut]
    evidence: list[EvidenceOut]


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=1, max_length=REFERENCE_MAX)
    pin: str = Field(min_length=1, max_length=16)
    # Only on a shared kitchen device: who is confirming.
    confirmer_id: uuid.UUID | None = None


class LateReviewRequest(VerifyRequest):
    decision: LateDecision
    # Required to refund; the customer sees it.
    reason: str | None = Field(default=None, max_length=REASON_MAX)


class ReverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin: str = Field(min_length=1, max_length=16)
    reason: str = Field(min_length=1, max_length=REASON_MAX)


class CodBlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=REASON_MAX)


class ReconciliationLineOut(BaseModel):
    payment_id: uuid.UUID
    order_id: uuid.UUID
    order_reference: str
    order_status: str
    amount_paisa: int
    reference: str
    verified_by_staff_id: uuid.UUID
    verified_by_name: str
    verified_at: datetime
    late: bool
    reversed_at: datetime | None
    reversed_by_name: str | None
    reversal_reason: str | None


class ReconciliationOut(BaseModel):
    day: date
    starts_at: datetime
    ends_at: datetime
    lines: list[ReconciliationLineOut]
    verified_count: int
    verified_paisa: int
    reversed_paisa: int
    net_paisa: int


def _out(view: PaymentView) -> PaymentOut:
    return PaymentOut.model_validate(view, from_attributes=True)


@router.get("/orders/{order_id}/payment")
async def get_payment(
    order_id: uuid.UUID, session: OrdersViewer, services: ServicesDep
) -> PaymentOut:
    return _out(await services.payments.view(session, order_id))


@router.post("/orders/{order_id}/payment/verify")
async def verify_payment(
    order_id: uuid.UUID, body: VerifyRequest, session: OrdersUpdater, services: ServicesDep
) -> PaymentOut:
    view = await services.payments.verify(
        session, order_id, reference=body.reference, pin=body.pin, confirmer_id=body.confirmer_id
    )
    return _out(view)


@router.post("/orders/{order_id}/payment/late-review")
async def review_late_payment(
    order_id: uuid.UUID, body: LateReviewRequest, session: OrdersUpdater, services: ServicesDep
) -> PaymentOut:
    view = await services.payments.review_late(
        session,
        order_id,
        decision=body.decision,
        reference=body.reference,
        pin=body.pin,
        confirmer_id=body.confirmer_id,
        reason=body.reason,
    )
    return _out(view)


@router.post("/payments/{payment_id}/reverse")
async def reverse_payment(
    payment_id: uuid.UUID, body: ReverseRequest, session: Reverser, services: ServicesDep
) -> PaymentOut:
    return _out(
        await services.payments.reverse(session, payment_id, pin=body.pin, reason=body.reason)
    )


@router.get("/payments/confirmers")
async def confirmers(session: OrdersUpdater, services: ServicesDep) -> list[ConfirmerOut]:
    found = await services.staff.confirmers(session)
    return [ConfirmerOut(id=m.id, display_name=m.display_name) for m in found]


@router.get("/payments/reconciliation")
async def reconciliation(
    session: Reconciler, services: ServicesDep, day: date | None = None
) -> ReconciliationOut:
    """`day` is a Pakistan date (YYYY-MM-DD); today by default."""
    chosen = day or services.clock.now().astimezone(PKT).date()
    found = await services.payments.reconciliation(session, chosen)
    return ReconciliationOut.model_validate(found, from_attributes=True)


@router.put("/customers/{customer_id}/cod-block", status_code=status.HTTP_204_NO_CONTENT)
async def block_cod(
    customer_id: uuid.UUID, body: CodBlockRequest, session: CodBlocker, services: ServicesDep
) -> Response:
    await services.payments.set_cod_block(session, customer_id, reason=body.reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/customers/{customer_id}/cod-block", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_cod(
    customer_id: uuid.UUID, session: CodBlocker, services: ServicesDep
) -> Response:
    await services.payments.set_cod_block(session, customer_id, reason=None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
