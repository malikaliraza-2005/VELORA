"""Order states and the transition engine (P7-T3, AD-20).

`TRANSITIONS` is the whole table: which status may follow which, and who may make the move.
Some moves depend on how the order is paid or fulfilled (a paid transfer order is refunded,
not cancelled; only a delivery goes out for delivery). Migration 0009 enforces the same pairs
in the database, and a test checks that the two agree.

`transition` is the only writer of `orders.status` after an order is created:
- the move must be in the table for that actor;
- the update is guarded (`UPDATE … WHERE status = <the status we read>`), so of two racing
  moves only the first wins and the other gets `StaleTransitionError`;
- every move writes an `order_transitions` row and an `audit_log` row;
- the customer's status message is queued in the same transaction (`notify`), and nothing
  waits for it to be delivered.

`draft` and `awaiting_confirmation` exist in the schema but no move uses them: on WhatsApp the
templated summary and its YES nonce are the confirmation step, and an order is created already
confirmed (`cod_confirmed` or `awaiting_transfer`). `paid_staff_verified` is set only by the
P8 payment verification (transaction reference and PIN), never by the generic staff action.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, get_args

from psycopg import AsyncConnection

from velora.db import TenantId
from velora.identity import audit
from velora.identity.errors import expect_row
from velora.ordering import gate
from velora.ordering.pricing import Fulfilment, PaymentMethod
from velora.orders import notify

Status = Literal[
    "draft",
    "awaiting_confirmation",
    "cod_confirmed",
    "awaiting_transfer",
    "paid_staff_verified",
    "accepted",
    "rejected",
    "preparing",
    "out_for_delivery",
    "ready",
    "completed",
    "cancelled",
    "expired",
    "refund_pending",
]
STATUSES: tuple[Status, ...] = get_args(Status)
ActorKind = Literal["customer", "staff", "system"]

CUSTOMER: frozenset[ActorKind] = frozenset({"customer"})
STAFF: frozenset[ActorKind] = frozenset({"staff"})
SYSTEM: frozenset[ActorKind] = frozenset({"system"})
CUSTOMER_OR_STAFF: frozenset[ActorKind] = frozenset({"customer", "staff"})


@dataclass(frozen=True)
class Rule:
    from_status: Status
    to_status: Status
    actors: frozenset[ActorKind]
    payment_method: PaymentMethod | None = None
    fulfilment: Fulfilment | None = None


TRANSITIONS: tuple[Rule, ...] = (
    Rule("cod_confirmed", "accepted", STAFF),
    Rule("cod_confirmed", "rejected", STAFF),
    Rule("cod_confirmed", "cancelled", CUSTOMER_OR_STAFF),
    Rule("awaiting_transfer", "paid_staff_verified", STAFF),
    Rule("awaiting_transfer", "expired", SYSTEM),
    Rule("awaiting_transfer", "cancelled", CUSTOMER_OR_STAFF),
    Rule("paid_staff_verified", "accepted", STAFF),
    Rule("paid_staff_verified", "refund_pending", STAFF),
    Rule("accepted", "preparing", STAFF),
    Rule("accepted", "cancelled", STAFF, payment_method="cod"),
    Rule("accepted", "refund_pending", STAFF, payment_method="transfer"),
    Rule("preparing", "out_for_delivery", STAFF, fulfilment="delivery"),
    Rule("preparing", "ready", STAFF, fulfilment="takeaway"),
    Rule("preparing", "cancelled", STAFF, payment_method="cod"),
    Rule("preparing", "refund_pending", STAFF, payment_method="transfer"),
    Rule("out_for_delivery", "completed", STAFF),
    Rule("ready", "completed", STAFF),
    Rule("refund_pending", "cancelled", STAFF),
)

# How an order starts: the customer's YES, by payment method (P7-T2).
INITIAL_STATUS: dict[PaymentMethod, Status] = {
    "cod": "cod_confirmed",
    "transfer": "awaiting_transfer",
}
# Staff must say why they refuse or cancel an order; the customer is told the reason.
REASON_REQUIRED: frozenset[Status] = frozenset({"rejected", "cancelled", "refund_pending"})
# Moves the generic staff action can't make (P8 sets it, with a reference and PIN).
STAFF_ACTION_EXCLUDED: frozenset[Status] = frozenset({"paid_staff_verified"})
# Before the kitchen starts. Ending an order here gives its capacity place back.
BEFORE_KITCHEN: frozenset[Status] = frozenset(
    {"cod_confirmed", "awaiting_transfer", "paid_staff_verified", "accepted"}
)
ENDED_UNSERVED: frozenset[Status] = frozenset({"rejected", "cancelled", "expired"})
TERMINAL: frozenset[Status] = frozenset({"rejected", "completed", "cancelled", "expired"})
REASON_MAX = 200


def order_reference(prefix: str, number: int) -> str:
    return f"{prefix}-{number:04d}"


def find_rule(
    from_status: str, to_status: str, *, payment_method: str | None, fulfilment: str
) -> Rule | None:
    for rule in TRANSITIONS:
        if (rule.from_status, rule.to_status) != (from_status, to_status):
            continue
        if rule.payment_method is not None and rule.payment_method != payment_method:
            continue
        if rule.fulfilment is not None and rule.fulfilment != fulfilment:
            continue
        return rule
    return None


def next_statuses(
    from_status: str, actor: ActorKind, *, payment_method: str | None, fulfilment: str
) -> list[Status]:
    """The moves `actor` may make from `from_status`, in table order (for the dashboard)."""
    return [
        rule.to_status
        for rule in TRANSITIONS
        if rule.from_status == from_status
        and actor in rule.actors
        and find_rule(
            from_status, rule.to_status, payment_method=payment_method, fulfilment=fulfilment
        )
        is rule
    ]


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    # The staff member, or the customer, who made the move. None for the system.
    id: uuid.UUID | None = None


SYSTEM_ACTOR = Actor("system")


class TransitionError(Exception):
    """A move was refused. The message is safe to show to staff."""


class OrderNotFoundError(TransitionError):
    pass


class IllegalTransitionError(TransitionError):
    pass


class InvalidReasonError(TransitionError):
    """The reason is missing where one is required, or too long."""


class StaleTransitionError(TransitionError):
    """The order changed since it was read (another move won the race)."""


@dataclass(frozen=True)
class OrderRef:
    id: uuid.UUID
    reference: str
    status: Status
    payment_method: PaymentMethod | None
    fulfilment: Fulfilment
    customer_id: uuid.UUID
    conversation_id: uuid.UUID | None
    capacity_slot: datetime | None
    total_paisa: int


async def load_order(
    conn: AsyncConnection[Any], order_id: uuid.UUID, *, lock: bool = False
) -> OrderRef | None:
    cur = await conn.execute(
        "SELECT o.id, o.order_number, t.order_number_prefix, o.status, o.payment_method, "
        "o.fulfilment, o.customer_id, o.conversation_id, o.capacity_slot, o.total_paisa "
        "FROM velora.orders o JOIN velora.tenants t ON t.id = o.tenant_id WHERE o.id = %s"
        + (" FOR UPDATE OF o" if lock else ""),
        (order_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    oid, number, prefix, status, payment, fulfilment, customer, conversation, slot, total = row
    return OrderRef(
        oid,
        order_reference(prefix, number),
        status,
        payment,
        fulfilment,
        customer,
        conversation,
        slot,
        total,
    )


async def transition(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    order_id: uuid.UUID,
    *,
    to: Status,
    actor: Actor,
    now: datetime,
    expected: Status | None = None,
    reason: str | None = None,
) -> OrderRef:
    """Move an order to `to`, in the caller's transaction. Returns the order as it now is."""
    order = await load_order(conn, order_id)
    if order is None:
        raise OrderNotFoundError("order not found")
    if expected is not None and order.status != expected:
        raise StaleTransitionError(f"the order is now {order.status}, not {expected}")
    rule = find_rule(
        order.status, to, payment_method=order.payment_method, fulfilment=order.fulfilment
    )
    if rule is None or actor.kind not in rule.actors:
        raise IllegalTransitionError(f"an order that is {order.status} can't become {to}")
    reason = " ".join((reason or "").split()) or None
    if reason is not None and len(reason) > REASON_MAX:
        raise InvalidReasonError(f"a reason is at most {REASON_MAX} characters")
    if actor.kind == "staff" and to in REASON_REQUIRED and reason is None:
        raise InvalidReasonError(f"say why the order is {to}")
    cur = await conn.execute(
        "UPDATE velora.orders SET status = %s, version = version + 1, updated_at = %s "
        "WHERE id = %s AND status = %s",
        (to, now, order_id, order.status),
    )
    if cur.rowcount != 1:
        raise StaleTransitionError("the order changed at the same moment; reload it")
    cur = await conn.execute(
        "INSERT INTO velora.order_transitions (tenant_id, order_id, from_status, to_status, "
        "actor_kind, actor_staff_id, reason, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id",
        (
            tenant.value,
            order_id,
            order.status,
            to,
            actor.kind,
            actor.id if actor.kind == "staff" else None,
            reason,
            now,
        ),
    )
    (transition_id,) = expect_row(await cur.fetchone())
    await audit.record(
        conn,
        tenant,
        at=now,
        actor_kind=actor.kind,
        actor_id=actor.id,
        action="order.status_changed",
        target_type="order",
        target_id=order_id,
        details={"from": order.status, "to": to, "reason": reason},
    )
    if order.status in BEFORE_KITCHEN and to in ENDED_UNSERVED:
        await release_capacity(conn, order.capacity_slot, now)
    await notify.status_changed(
        conn,
        tenant,
        reference=order.reference,
        conversation_id=order.conversation_id,
        to=to,
        by_customer=actor.kind == "customer",
        reason=reason,
        now=now,
        key=f"order-status:{transition_id}",
    )
    return replace(order, status=to)


async def release_capacity(
    conn: AsyncConnection[Any], slot: datetime | None, now: datetime
) -> None:
    """Give back the order's place if its slot is still the current one. Past slots are over,
    so their counts stay as history."""
    if slot is None or slot != gate.slot_start(now):
        return
    await conn.execute(
        "UPDATE velora.order_capacity_slots SET orders_count = orders_count - 1 "
        "WHERE slot_start = %s AND orders_count > 0",
        (slot,),
    )
