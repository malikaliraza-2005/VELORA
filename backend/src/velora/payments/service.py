"""Staff payment actions (P8-T1, T2, T4, T5, T6; AD-03, AD-23, AD-32).

**The only way an order becomes paid** is `verify` (or `review_late` with "accept"): a staff
member types the transaction reference from the restaurant's own bank or wallet app and their
personal PIN. In one transaction it then
1. locks the order, which must still be `awaiting_transfer` (or `expired`, for a late review);
2. refuses a reference already verified for another order of this restaurant (duplicates are
   flagged in the audit log, and staff see which order has it);
3. appends a `verified` entry to the payments ledger (amount = the order total, the confirmer,
   the device it was done on);
4. moves the order with `lifecycle.transition`, which writes its history and audit rows. The
   database refuses `paid_staff_verified` without that ledger entry.

The PIN is checked first, in its own transaction, so a wrong PIN is counted and audited even
though nothing else happens (`StaffService.verify_payment_pin`).

**Late payments (P8-T5).** An expired transfer order can only be reviewed: "accept" verifies the
payment and accepts the order; "refund" verifies it (the money did arrive) and marks the order
`refund_pending`, for a manual refund from the restaurant's own account.

**Owner controls (P8-T6).** Only the owner sees the daily reconciliation report and can
reverse a verification (with their PIN and a reason). A reversal is a new ledger entry; an order
still `paid_staff_verified` goes back to `awaiting_transfer`. An order already further on keeps
its status, and the owner decides what to do with it.

VELORA never holds or moves money: every entry records money staff saw arrive in the
restaurant's own account.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection, errors

from velora.clock import Clock
from velora.conversation.pktime import PKT
from velora.db import Database
from velora.identity import audit
from velora.identity.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    expect_row,
)
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.identity.staff import StaffService
from velora.orders import lifecycle
from velora.orders.lifecycle import Actor, OrderRef, Status

REFERENCE_MAX = 64
REASON_MAX = lifecycle.REASON_MAX
_KEY_CHARS = re.compile(r"[^A-Z0-9]")
_KEY_MIN = 4

LateDecision = Literal["accept", "refund"]
PaymentAction = Literal["verify", "late_review"]


class DuplicateReferenceError(ConflictError):
    """The reference was already verified for another order. The attempt is audited."""

    def __init__(self, reference: str, other_order: str) -> None:
        super().__init__(
            f"reference {reference} is already recorded for order #{other_order}; "
            "check the bank or wallet app again"
        )
        self.other_order = other_order


def reference_key(reference: str) -> tuple[str, str]:
    """(the reference as stored, its comparison key). Raises InvalidInputError."""
    cleaned = " ".join(reference.split())
    if not cleaned or len(cleaned) > REFERENCE_MAX:
        raise InvalidInputError(f"a transaction reference is 1 to {REFERENCE_MAX} characters")
    key = _KEY_CHARS.sub("", cleaned.upper())
    if len(key) < _KEY_MIN:
        raise InvalidInputError(
            f"a transaction reference has at least {_KEY_MIN} letters or digits"
        )
    return cleaned, key


def _reason(reason: str | None, *, required: bool) -> str | None:
    cleaned = " ".join((reason or "").split()) or None
    if cleaned is None and required:
        raise InvalidInputError("say why")
    if cleaned is not None and len(cleaned) > REASON_MAX:
        raise InvalidInputError(f"a reason is at most {REASON_MAX} characters")
    return cleaned


def _require(actor: StaffSession, permission: Permission) -> None:
    if not actor.can(permission):
        raise ForbiddenError(f"your role can't do this ({permission})")


@dataclass(frozen=True)
class LedgerEntry:
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


@dataclass(frozen=True)
class Evidence:
    id: uuid.UUID
    kind: str
    provider_media_id: str | None
    mime_type: str | None
    received_at: datetime


@dataclass(frozen=True)
class PaymentView:
    order_id: uuid.UUID
    reference: str
    status: str
    payment_method: str | None
    total_paisa: int
    payment_due_at: datetime | None
    customer_id: uuid.UUID
    cod_blocked: bool
    # What staff may do about the payment now.
    actions: list[PaymentAction]
    ledger: list[LedgerEntry]
    evidence: list[Evidence]


@dataclass(frozen=True)
class ReconciliationLine:
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


@dataclass(frozen=True)
class Reconciliation:
    day: date
    starts_at: datetime
    ends_at: datetime
    lines: list[ReconciliationLine]
    verified_count: int
    verified_paisa: int
    reversed_paisa: int
    net_paisa: int


_LEDGER = (
    "SELECT p.id, p.status, p.method, p.amount_paisa, p.reference, p.verified_by_staff_id, "
    "s.display_name, p.verified_at, p.late, p.corrects_payment_id, p.reason, p.created_at "
    "FROM velora.payments p LEFT JOIN velora.staff_members s ON s.id = p.verified_by_staff_id "
)


def payment_actions(status: str, payment_method: str | None) -> list[PaymentAction]:
    if payment_method != "transfer":
        return []
    if status == "awaiting_transfer":
        return ["verify"]
    if status == "expired":
        return ["late_review"]
    return []


class PaymentService:
    def __init__(self, db: Database, clock: Clock, staff: StaffService) -> None:
        self._db = db
        self._clock = clock
        self._staff = staff

    # Viewing -------------------------------------------------------------------------------

    async def view(self, actor: StaffSession, order_id: uuid.UUID) -> PaymentView:
        _require(actor, Permission.ORDERS_VIEW)
        async with self._db.tenant(actor.tenant) as conn:
            order = await lifecycle.load_order(conn, order_id)
            if order is None:
                raise NotFoundError("order not found")
            cur = await conn.execute(
                "SELECT o.payment_due_at, cu.cod_blocked_at IS NOT NULL FROM velora.orders o "
                "JOIN velora.customers cu ON cu.id = o.customer_id WHERE o.id = %s",
                (order_id,),
            )
            due, blocked = expect_row(await cur.fetchone())
            cur = await conn.execute(
                _LEDGER  # noqa: S608 - a fixed fragment
                + "WHERE p.order_id = %s ORDER BY p.created_at, p.status = 'reversed', p.id",
                (order_id,),
            )
            ledger = [LedgerEntry(*row) for row in await cur.fetchall()]
            cur = await conn.execute(
                "SELECT id, kind, provider_media_id, mime_type, received_at "
                "FROM velora.payment_evidence WHERE order_id = %s ORDER BY received_at, id",
                (order_id,),
            )
            found = [Evidence(*row) for row in await cur.fetchall()]
        return PaymentView(
            order_id=order.id,
            reference=order.reference,
            status=order.status,
            payment_method=order.payment_method,
            total_paisa=order.total_paisa,
            payment_due_at=due,
            customer_id=order.customer_id,
            cod_blocked=blocked,
            actions=payment_actions(order.status, order.payment_method),
            ledger=ledger,
            evidence=found,
        )

    # Verification (P8-T4) and late payments (P8-T5) --------------------------------------------

    async def verify(
        self,
        actor: StaffSession,
        order_id: uuid.UUID,
        *,
        reference: str,
        pin: str,
        confirmer_id: uuid.UUID | None = None,
    ) -> PaymentView:
        """Staff saw the transfer in the restaurant's account: the order becomes
        `paid_staff_verified`."""
        await self._confirm(
            actor,
            order_id,
            reference=reference,
            pin=pin,
            confirmer_id=confirmer_id,
            expected="awaiting_transfer",
            moves=[("paid_staff_verified", None)],
        )
        return await self.view(actor, order_id)

    async def review_late(
        self,
        actor: StaffSession,
        order_id: uuid.UUID,
        *,
        decision: LateDecision,
        reference: str,
        pin: str,
        confirmer_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> PaymentView:
        """A transfer that arrived after the order expired: accept the order, or refund."""
        if decision == "accept":
            moves: list[tuple[Status, str | None]] = [
                ("paid_staff_verified", None),
                ("accepted", None),
            ]
        elif decision == "refund":
            moves = [("refund_pending", _reason(reason, required=True))]
        else:
            raise InvalidInputError('a late payment is either "accept" or "refund"')
        await self._confirm(
            actor,
            order_id,
            reference=reference,
            pin=pin,
            confirmer_id=confirmer_id,
            expected="expired",
            moves=moves,
        )
        return await self.view(actor, order_id)

    async def _confirm(
        self,
        actor: StaffSession,
        order_id: uuid.UUID,
        *,
        reference: str,
        pin: str,
        confirmer_id: uuid.UUID | None,
        expected: Status,
        moves: list[tuple[Status, str | None]],
    ) -> None:
        _require(actor, Permission.ORDERS_UPDATE)
        stored, key = reference_key(reference)
        # Refuse what can't succeed before spending one of the confirmer's PIN attempts.
        async with self._db.tenant(actor.tenant) as conn:
            await self._payable(conn, order_id, expected)
        confirmer = await self._staff.verify_payment_pin(actor, pin=pin, confirmer_id=confirmer_id)
        now = self._clock.now()
        duplicate: str | None = None
        async with self._db.tenant(actor.tenant) as conn:
            order = await self._payable(conn, order_id, expected, lock=True)
            # Two confirmations with the same reference wait for each other here.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"payment-reference:{actor.tenant.value}:{key}",),
            )
            duplicate = await self._verified_elsewhere(conn, key)
            if duplicate is not None:
                await audit.record(
                    conn,
                    actor.tenant,
                    at=now,
                    actor_kind="staff",
                    actor_id=actor.staff_id,
                    action="payment.duplicate_reference",
                    target_type="order",
                    target_id=order_id,
                    details={
                        "reference": stored,
                        "already_on": duplicate,
                        "confirmer_id": str(confirmer),
                    },
                )
            else:
                await self._record_verified(
                    conn, actor, order, stored, key, confirmer, now, late=expected == "expired"
                )
                status = expected
                for to, why in moves:
                    try:
                        await lifecycle.transition(
                            conn,
                            actor.tenant,
                            order_id,
                            to=to,
                            actor=Actor("staff", confirmer),
                            now=now,
                            expected=status,
                            reason=why,
                        )
                    except lifecycle.TransitionError as exc:
                        raise ConflictError(str(exc)) from exc
                    status = to
        if duplicate is not None:
            raise DuplicateReferenceError(stored, duplicate)

    async def _payable(
        self,
        conn: AsyncConnection[Any],
        order_id: uuid.UUID,
        expected: Status,
        *,
        lock: bool = False,
    ) -> OrderRef:
        order = await lifecycle.load_order(conn, order_id, lock=lock)
        if order is None:
            raise NotFoundError("order not found")
        if order.payment_method != "transfer":
            raise ConflictError("only transfer orders are verified here")
        if order.status != expected:
            what = {
                "awaiting_transfer": "waiting for a transfer",
                "expired": "expired (late payments are reviewed separately)",
            }
            raise ConflictError(
                f"order #{order.reference} is {order.status}, not {what.get(expected, expected)}"
            )
        return order

    async def _verified_elsewhere(self, conn: AsyncConnection[Any], key: str) -> str | None:
        """The order that already has this reference verified (and not reversed), if any."""
        cur = await conn.execute(
            "SELECT o.order_number, t.order_number_prefix FROM velora.payments p "
            "JOIN velora.orders o ON o.id = p.order_id "
            "JOIN velora.tenants t ON t.id = o.tenant_id "
            "WHERE p.reference_key = %s AND p.status = 'verified' AND NOT EXISTS ("
            "SELECT 1 FROM velora.payments r WHERE r.corrects_payment_id = p.id "
            "AND r.status = 'reversed') LIMIT 1",
            (key,),
        )
        row = await cur.fetchone()
        return None if row is None else lifecycle.order_reference(row[1], row[0])

    async def _record_verified(
        self,
        conn: AsyncConnection[Any],
        actor: StaffSession,
        order: OrderRef,
        reference: str,
        key: str,
        confirmer: uuid.UUID,
        now: datetime,
        *,
        late: bool,
    ) -> uuid.UUID:
        cur = await conn.execute(
            "INSERT INTO velora.payments (tenant_id, order_id, method, amount_paisa, status, "
            "reference, reference_key, verified_by_staff_id, verified_at, recorded_by_staff_id, "
            "late, created_at) VALUES (%s, %s, 'transfer', %s, 'verified', %s, %s, %s, %s, %s, "
            "%s, %s) RETURNING id",
            (
                actor.tenant.value,
                order.id,
                order.total_paisa,
                reference,
                key,
                confirmer,
                now,
                actor.staff_id,
                late,
                now,
            ),
        )
        payment_id: uuid.UUID = expect_row(await cur.fetchone())[0]
        await audit.record(
            conn,
            actor.tenant,
            at=now,
            actor_kind="staff",
            actor_id=actor.staff_id,
            action="payment.verified",
            target_type="order",
            target_id=order.id,
            details={
                "payment_id": str(payment_id),
                "reference": reference,
                "amount_paisa": order.total_paisa,
                "confirmer_id": str(confirmer),
                "session_id": str(actor.session_id),
                "late": late,
            },
        )
        return payment_id

    # Owner controls (P8-T6) ------------------------------------------------------------------

    async def reverse(
        self, actor: StaffSession, payment_id: uuid.UUID, *, pin: str, reason: str
    ) -> PaymentView:
        """The owner withdraws a verification: a `reversed` ledger entry, audited."""
        _require(actor, Permission.PAYMENTS_REVERSE)
        why = _reason(reason, required=True)
        async with self._db.tenant(actor.tenant) as conn:
            order_id = await self._reversible(conn, payment_id)
        owner = await self._staff.verify_payment_pin(actor, pin=pin)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            # The order row is the lock: ledger rows can't be locked (they can't be updated).
            order = await lifecycle.load_order(conn, order_id, lock=True)
            if order is None:
                raise NotFoundError("order not found")
            await self._reversible(conn, payment_id)
            cur = await conn.execute(
                "SELECT method, amount_paisa FROM velora.payments WHERE id = %s", (payment_id,)
            )
            method, amount = expect_row(await cur.fetchone())
            try:
                await conn.execute(
                    "INSERT INTO velora.payments (tenant_id, order_id, method, amount_paisa, "
                    "status, verified_by_staff_id, verified_at, recorded_by_staff_id, "
                    "corrects_payment_id, reason, created_at) "
                    "VALUES (%s, %s, %s, %s, 'reversed', %s, %s, %s, %s, %s, %s)",
                    (
                        actor.tenant.value,
                        order_id,
                        method,
                        amount,
                        owner,
                        now,
                        actor.staff_id,
                        payment_id,
                        why,
                        now,
                    ),
                )
            except errors.UniqueViolation as exc:
                raise ConflictError("this payment is already reversed") from exc
            await audit.record(
                conn,
                actor.tenant,
                at=now,
                actor_kind="staff",
                actor_id=actor.staff_id,
                action="payment.reversed",
                target_type="order",
                target_id=order_id,
                details={
                    "payment_id": str(payment_id),
                    "amount_paisa": amount,
                    "reason": why,
                    "order_status": order.status,
                },
            )
            if order.status == "paid_staff_verified":
                await lifecycle.transition(
                    conn,
                    actor.tenant,
                    order_id,
                    to="awaiting_transfer",
                    actor=Actor("staff", owner),
                    now=now,
                    expected="paid_staff_verified",
                    reason=why,
                )
        return await self.view(actor, order_id)

    async def _reversible(self, conn: AsyncConnection[Any], payment_id: uuid.UUID) -> uuid.UUID:
        cur = await conn.execute(
            "SELECT p.order_id, p.status, EXISTS (SELECT 1 FROM velora.payments r "
            "WHERE r.corrects_payment_id = p.id) FROM velora.payments p WHERE p.id = %s",
            (payment_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise NotFoundError("payment not found")
        order_id, status, reversed_already = row
        if status != "verified":
            raise ConflictError("only a verified payment can be reversed")
        if reversed_already:
            raise ConflictError("this payment is already reversed")
        return order_id  # type: ignore[no-any-return]

    async def reconciliation(self, actor: StaffSession, day: date) -> Reconciliation:
        """Every transfer verified on `day` (Pakistan time), with any reversal, for the owner."""
        _require(actor, Permission.PAYMENTS_RECONCILE)
        starts = datetime.combine(day, time(0), PKT)
        ends = starts + timedelta(days=1)
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT p.id, o.id, o.order_number, t.order_number_prefix, o.status, "
                "p.amount_paisa, p.reference, p.verified_by_staff_id, s.display_name, "
                "p.verified_at, p.late, r.verified_at, rs.display_name, r.reason "
                "FROM velora.payments p "
                "JOIN velora.orders o ON o.id = p.order_id "
                "JOIN velora.tenants t ON t.id = o.tenant_id "
                "JOIN velora.staff_members s ON s.id = p.verified_by_staff_id "
                "LEFT JOIN velora.payments r ON r.corrects_payment_id = p.id "
                "AND r.status = 'reversed' "
                "LEFT JOIN velora.staff_members rs ON rs.id = r.verified_by_staff_id "
                "WHERE p.status = 'verified' AND p.method = 'transfer' "
                "AND p.verified_at >= %s AND p.verified_at < %s "
                "ORDER BY p.verified_at, p.id",
                (starts, ends),
            )
            lines = [
                ReconciliationLine(
                    payment_id=r[0],
                    order_id=r[1],
                    order_reference=lifecycle.order_reference(r[3], r[2]),
                    order_status=r[4],
                    amount_paisa=r[5],
                    reference=r[6],
                    verified_by_staff_id=r[7],
                    verified_by_name=r[8],
                    verified_at=r[9],
                    late=r[10],
                    reversed_at=r[11],
                    reversed_by_name=r[12],
                    reversal_reason=r[13],
                )
                for r in await cur.fetchall()
            ]
        verified = sum(line.amount_paisa for line in lines)
        reversed_ = sum(line.amount_paisa for line in lines if line.reversed_at is not None)
        return Reconciliation(
            day=day,
            starts_at=starts,
            ends_at=ends,
            lines=lines,
            verified_count=len(lines),
            verified_paisa=verified,
            reversed_paisa=reversed_,
            net_paisa=verified - reversed_,
        )

    # COD blocklist (P8-T2) -------------------------------------------------------------------

    async def set_cod_block(
        self, actor: StaffSession, customer_id: uuid.UUID, *, reason: str | None
    ) -> bool:
        """Block (with a reason) or, with None, unblock cash on delivery for a customer.
        Returns whether the customer is now blocked."""
        _require(actor, Permission.CUSTOMERS_BLOCK_COD)
        blocking = reason is not None
        why = _reason(reason, required=True) if blocking else None
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT cod_block_reason FROM velora.customers WHERE id = %s FOR UPDATE",
                (customer_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise NotFoundError("customer not found")
            if row[0] == why:
                return blocking
            await conn.execute(
                "UPDATE velora.customers SET cod_blocked_at = %s, cod_block_reason = %s, "
                "updated_at = %s WHERE id = %s",
                (now if blocking else None, why, now, customer_id),
            )
            await audit.record(
                conn,
                actor.tenant,
                at=now,
                actor_kind="staff",
                actor_id=actor.staff_id,
                action="customer.cod_blocked" if blocking else "customer.cod_unblocked",
                target_type="customer",
                target_id=customer_id,
                details={"reason": why, "previous_reason": row[0]},
            )
        return blocking
