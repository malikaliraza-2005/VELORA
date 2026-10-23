"""Staff side of orders (P7-T3): list, view and move orders, plus the dashboard's late
payments list and today summary (P9).

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
from datetime import date, datetime, time, timedelta
from typing import Any

from velora.clock import Clock
from velora.conversation.pktime import PKT
from velora.db import Database
from velora.identity.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    expect_row,
)
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.orders import lifecycle
from velora.orders.lifecycle import Actor, Status

LIST_LIMIT = 200
# Expired transfer orders shown for late review. Staff have no time limit (AD-32); this only
# bounds the list.
LATE_WINDOW = timedelta(days=7)


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
    placed_during_recovery: bool
    # What this staff member may move it to next.
    next_statuses: list[str]
    lines: list[OrderLine]
    history: list[OrderMove]


_COLUMNS = (
    "o.id, o.order_number, t.order_number_prefix, o.status, o.fulfilment, o.payment_method, "
    "o.subtotal_paisa, o.delivery_fee_paisa, o.tax_paisa, o.total_paisa, o.delivery_address, "
    "o.delivery_note, o.pickup_at, o.payment_due_at, o.possible_duplicate, cu.phone_e164, "
    "o.created_at, o.updated_at, "
    # P11 step 9: placed after a restart, before the messages held through the outage were
    # all processed. Staff check these with the customer.
    "EXISTS (SELECT 1 FROM velora.recovery_runs r WHERE r.tenant_id = o.tenant_id "
    "AND o.created_at >= r.started_at "
    "AND (r.backlog_cleared_at IS NULL OR o.created_at <= r.backlog_cleared_at))"
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
        during_recovery,
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
        placed_during_recovery=during_recovery,
        next_statuses=allowed,
        lines=lines,
        history=history,
    )


@dataclass(frozen=True)
class LatePayment:
    """An expired transfer order: the customer may still have paid (P8-T5)."""

    order: OrderView
    evidence_count: int


@dataclass(frozen=True)
class TodaySummary:
    """One Pakistan day, from the orders placed that day (P9-T5)."""

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
    # Transfers verified today (any order), less today's reversals; the owner's report has
    # the detail.
    transfers_verified_paisa: int
    handoffs_waiting: int


class OrderService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def late_payments(self, actor: StaffSession) -> list[LatePayment]:
        """Expired transfer orders from the last `LATE_WINDOW`, newest first, with how much
        evidence the customer sent. They aren't in the active list."""
        _require(actor, Permission.ORDERS_VIEW)
        since = self._clock.now() - LATE_WINDOW
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS}, (SELECT count(*) FROM velora.payment_evidence e "  # noqa: S608 - fixed fragments
                f"WHERE e.order_id = o.id) {_FROM} WHERE o.status = 'expired' "
                "AND o.payment_method = 'transfer' AND o.updated_at >= %s "
                f"ORDER BY o.updated_at DESC LIMIT {LIST_LIMIT}",
                (since,),
            )
            return [LatePayment(_view(row[:-1], [], []), row[-1]) for row in await cur.fetchall()]

    async def today(self, actor: StaffSession, day: date) -> TodaySummary:
        _require(actor, Permission.REPORTS_VIEW)
        starts = datetime.combine(day, time(0), PKT)
        ends = starts + timedelta(days=1)
        terminal = sorted(lifecycle.TERMINAL)
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT count(*), "
                "count(*) FILTER (WHERE NOT status = ANY(%(terminal)s)), "
                "count(*) FILTER (WHERE status = 'completed'), "
                "count(*) FILTER (WHERE status IN ('cancelled', 'rejected')), "
                "count(*) FILTER (WHERE status = 'expired'), "
                "COALESCE(sum(total_paisa) FILTER (WHERE status = 'completed'), 0), "
                "COALESCE(sum(total_paisa) FILTER (WHERE status = 'completed' "
                "AND payment_method = 'cod'), 0), "
                "COALESCE(sum(total_paisa) FILTER (WHERE status = 'completed' "
                "AND payment_method = 'transfer'), 0) "
                "FROM velora.orders WHERE created_at >= %(starts)s AND created_at < %(ends)s",
                {"terminal": terminal, "starts": starts, "ends": ends},
            )
            placed, open_, completed, cancelled, expired, paisa, cod, transfer = expect_row(
                await cur.fetchone()
            )
            cur = await conn.execute(
                "SELECT COALESCE(sum(CASE status WHEN 'verified' THEN amount_paisa "
                "ELSE -amount_paisa END), 0) FROM velora.payments "
                "WHERE method = 'transfer' AND verified_at >= %s AND verified_at < %s",
                (starts, ends),
            )
            (transfers,) = expect_row(await cur.fetchone())
            cur = await conn.execute(
                "SELECT count(*) FROM velora.conversations WHERE state = 'handoff'"
            )
            (handoffs,) = expect_row(await cur.fetchone())
        return TodaySummary(
            day=day,
            starts_at=starts,
            ends_at=ends,
            placed=placed,
            open=open_,
            completed=completed,
            cancelled=cancelled,
            expired=expired,
            completed_paisa=paisa,
            completed_cod_paisa=cod,
            completed_transfer_paisa=transfer,
            transfers_verified_paisa=transfers,
            handoffs_waiting=handoffs,
        )

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
