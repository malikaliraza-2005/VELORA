"""What a customer sends about a payment (P8-T3, AD-03): a screenshot, a document or a message
such as "I have paid".

It is attached to the customer's transfer order for staff to look at, and **nothing else
happens**: no payment status, no order status, no ledger entry. Only a staff member with the
transaction reference and a PIN can verify a payment (`velora.payments.service`).

Evidence attaches to the customer's latest transfer order that is still awaiting payment, or
that expired in the last 24 hours (a late payment for staff to review).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection

from velora.db import TenantId
from velora.identity import audit
from velora.orders.lifecycle import order_reference

EvidenceKind = Literal["image", "document", "text_claim"]
LATE_EVIDENCE_WINDOW = timedelta(hours=24)

# Message kinds kept as evidence when a transfer order is open.
MEDIA_KINDS: dict[str, EvidenceKind] = {"image": "image", "document": "document"}

# Deterministic "I have paid" phrases (normalised text). The AI's `payment_claim` intent is
# handled the same way.
_CLAIM = re.compile(
    r"\b(i have paid|i paid|ive paid|paid|payment (done|sent|kar ?d[iy]a?|ho gay?i)|"
    r"(pay|transfer|payment) kar ?d[iy]a?|transfer (done|ho gay?a)|bhej d[iy]a?|"
    r"paise bhej)\b"
)


def is_payment_claim(words: str) -> bool:
    return _CLAIM.search(words) is not None


@dataclass(frozen=True)
class EvidenceOrder:
    id: uuid.UUID
    reference: str
    expired: bool


async def order_for_evidence(
    conn: AsyncConnection[Any], customer_id: uuid.UUID, now: datetime
) -> EvidenceOrder | None:
    """The transfer order this customer's evidence belongs to, if any."""
    cur = await conn.execute(
        "SELECT o.id, o.order_number, t.order_number_prefix, o.status "
        "FROM velora.orders o JOIN velora.tenants t ON t.id = o.tenant_id "
        "WHERE o.customer_id = %s AND o.payment_method = 'transfer' "
        "AND (o.status = 'awaiting_transfer' OR (o.status = 'expired' AND o.updated_at > %s)) "
        "ORDER BY o.created_at DESC LIMIT 1",
        (customer_id, now - LATE_EVIDENCE_WINDOW),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    order_id, number, prefix, status = row
    return EvidenceOrder(order_id, order_reference(prefix, number), status == "expired")


async def record(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    order: EvidenceOrder,
    *,
    kind: EvidenceKind,
    customer_id: uuid.UUID,
    inbox_event_id: uuid.UUID,
    received_at: datetime,
    media: dict[str, Any] | None = None,
) -> None:
    """Attach evidence to `order`, once per inbound message. Changes no status."""
    media = media or {}
    cur = await conn.execute(
        "INSERT INTO velora.payment_evidence (tenant_id, order_id, kind, provider_media_id, "
        "mime_type, inbox_event_id, received_at) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, inbox_event_id) DO NOTHING RETURNING id",
        (
            tenant.value,
            order.id,
            kind,
            None if kind == "text_claim" else str(media.get("provider_media_id") or "unknown"),
            None if kind == "text_claim" else media.get("mime_type"),
            inbox_event_id,
            received_at,
        ),
    )
    row = await cur.fetchone()
    if row is None:
        return
    await audit.record(
        conn,
        tenant,
        at=received_at,
        actor_kind="customer",
        actor_id=customer_id,
        action="payment.evidence_received",
        target_type="order",
        target_id=order.id,
        details={"kind": kind, "evidence_id": str(row[0]), "order_expired": order.expired},
    )
