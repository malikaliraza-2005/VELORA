"""Staff side of orders (P7-T3): list, view and move orders. The dashboard screens are P9.

- Viewing needs `ORDERS_VIEW`; moving an order needs `ORDERS_UPDATE` (every staff role, so
  the kitchen can accept and progress orders).
- A move names the status the staff member saw (`expected`). If the order has moved since,
  it is refused (409), so a double tap or two devices can't apply a move twice (AD-20).
- Payment moves can't be made here (`lifecycle.PAYMENT_MOVES`): they need the transaction
  reference and a PIN, through `velora.payments`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from velora.clock import Clock
from velora.db import Database
from velora.identity.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.orders import lifecycle
from velora.orders.lifecycle import Actor, Status

LIST_LIMIT = 200


@dataclass(frozen=True)
class OrderLine:
    line_no: int
    sku: str
    name: str
    choices: list[list[str]]
    unit_price_paisa: int
    quantity: int
    line_total_paisa: int


@dataclass(frozen=True)
class OrderMove:
    from_status: str | None
    to_status: str
    actor_kind: str
    actor_staff_id: uuid.UUID | None
    reason: str | None
    at: datetime


@dataclass(frozen=True)
class OrderView:
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
    # What this staff member may move it to next.
    next_statuses: list[str]
    lines: list[OrderLine]
    history: list[OrderMove]


_COLUMNS = (
    "o.id, o.order_number, t.order_number_prefix, o.status, o.fulfilment, o.payment_method, "
    "o.subtotal_paisa, o.delivery_fee_paisa, o.tax_paisa, o.total_paisa, o.delivery_address, "
    "o.delivery_note, o.pickup_at, o.payment_due_at, o.possible_duplicate, cu.phone_e164, "
    "o.created_at, o.updated_at"
)
_FROM = (
    "FROM velora.orders o JOIN velora.tenants t ON t.id = o.tenant_id "
    "JOIN velora.customers cu ON cu.id = o.customer_id"
)


def _require(actor: StaffSession, permission: Permission) -> None:
    if not actor.can(permission):
        raise ForbiddenError(f"your role can't do this ({permission})")


def _view(row: tuple[Any, ...], lines: list[OrderLine], history: list[OrderMove]) -> OrderView:
    (
        oid,
        number,
        prefix,
        status,
        fulfilment,
        payment,
        subtotal,
        fee,
        tax,
        total,
        address,
        note,
        pickup_at,
        due,
        duplicate,
        phone,
        created_at,
        updated_at,
    ) = row
    allowed: list[str] = [
        s
        for s in lifecycle.next_statuses(
            status, "staff", payment_method=payment, fulfilment=fulfilment
        )
        if (status, s) not in lifecycle.PAYMENT_MOVES
    ]
    return OrderView(
        id=oid,
        reference=lifecycle.order_reference(prefix, number),
        status=status,
        fulfilment=fulfilment,
        payment_method=payment,
        subtotal_paisa=subtotal,
        delivery_fee_paisa=fee,
        tax_paisa=tax,
        total_paisa=total,
        delivery_address=address,
        delivery_note=note,
        pickup_at=pickup_at,
        payment_due_at=due,
        possible_duplicate=duplicate,
        customer_phone=phone,
        created_at=created_at,
        updated_at=updated_at,
        next_statuses=allowed,
        lines=lines,
        history=history,
    )


class OrderService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def list_orders(self, actor: StaffSession, *, active: bool = True) -> list[OrderView]:
        """Orders, newest first. `active`: only those that haven't ended."""
        _require(actor, Permission.ORDERS_VIEW)
        where = "WHERE NOT o.status = ANY(%s)" if active else ""
        params: tuple[Any, ...] = (sorted(lifecycle.TERMINAL),) if active else ()
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} {_FROM} {where} "  # noqa: S608 - fixed fragments
                f"ORDER BY o.created_at DESC LIMIT {LIST_LIMIT}",
                params,
            )
            return [_view(row, [], []) for row in await cur.fetchall()]

    async def get(self, actor: StaffSession, order_id: uuid.UUID) -> OrderView:
        _require(actor, Permission.ORDERS_VIEW)
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} {_FROM} WHERE o.id = %s",  # noqa: S608 - fixed fragments
                (order_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise NotFoundError("order not found")
            cur = await conn.execute(
                "SELECT line_no, sku, name_snapshot, choices, unit_price_paisa, quantity, "
                "line_total_paisa FROM velora.order_items WHERE order_id = %s ORDER BY line_no",
                (order_id,),
            )
            lines = [OrderLine(*r) for r in await cur.fetchall()]
            cur = await conn.execute(
                "SELECT from_status, to_status, actor_kind, actor_staff_id, reason, created_at "
                "FROM velora.order_transitions WHERE order_id = %s ORDER BY seq",
                (order_id,),
            )
            history = [OrderMove(*r) for r in await cur.fetchall()]
        return _view(row, lines, history)

    async def move(
        self,
        actor: StaffSession,
        order_id: uuid.UUID,
        *,
        to: Status,
        expected: Status,
        reason: str | None = None,
    ) -> OrderView:
        _require(actor, Permission.ORDERS_UPDATE)
        if (expected, to) in lifecycle.PAYMENT_MOVES or to == "paid_staff_verified":
            raise ForbiddenError("payments are verified with the reference and a PIN")
        async with self._db.tenant(actor.tenant) as conn:
            try:
                await lifecycle.transition(
                    conn,
                    actor.tenant,
                    order_id,
                    to=to,
                    actor=Actor("staff", actor.staff_id),
                    now=self._clock.now(),
                    expected=expected,
                    reason=reason,
                )
            except lifecycle.OrderNotFoundError as exc:
                raise NotFoundError(str(exc)) from exc
            except lifecycle.InvalidReasonError as exc:
                raise InvalidInputError(str(exc)) from exc
            except lifecycle.TransitionError as exc:
                raise ConflictError(str(exc)) from exc
        return await self.get(actor, order_id)
