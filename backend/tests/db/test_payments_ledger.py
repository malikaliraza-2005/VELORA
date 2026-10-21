"""P8-T1 and the database half of P8-T7 (migration 0010).

- The payments ledger is append-only: UPDATE and DELETE are refused for `app_rw` (and by a
  trigger for everyone); a correction is a new `reversed` row, at most one per entry.
- An order can't become `paid_staff_verified` (or, from `expired`, `refund_pending`) without a
  verified, unreversed ledger entry, even through a direct UPDATE.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.db.conftest import PgDatabase, SeededTenants
from tests.db.seed import insert_order

CHANGES = ["UPDATE velora.payments SET amount_paisa = 1", "DELETE FROM velora.payments"]


def _as_tenant(conn: psycopg.Connection[Any], tenant: UUID) -> None:
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))


def _verified(conn: psycopg.Connection[Any], tenant: UUID, order: UUID, staff: UUID) -> UUID:
    row = conn.execute(
        "INSERT INTO velora.payments (tenant_id, order_id, method, amount_paisa, status, "
        "reference, reference_key, verified_by_staff_id, verified_at) "
        "VALUES (%s, %s, 'transfer', 90000, 'verified', 'TXN-1', 'TXN1', %s, now()) "
        "RETURNING id",
        (tenant, order, staff),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _reverse(conn: psycopg.Connection[Any], tenant: UUID, payment: UUID, staff: UUID) -> None:
    conn.execute(
        "INSERT INTO velora.payments (tenant_id, order_id, method, amount_paisa, status, "
        "verified_by_staff_id, verified_at, corrects_payment_id, reason) "
        "SELECT tenant_id, order_id, method, amount_paisa, 'reversed', %s, now(), id, 'wrong' "
        "FROM velora.payments WHERE id = %s",
        (staff, payment),
    )


@pytest.mark.parametrize("statement", CHANGES)
def test_the_runtime_role_cannot_change_ledger_rows(
    db: PgDatabase, tenants: SeededTenants, statement: str
) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(statement.encode())


@pytest.mark.parametrize("statement", CHANGES)
def test_no_one_can_change_ledger_rows(
    db: PgDatabase, tenants: SeededTenants, statement: str
) -> None:
    with (
        db.admin() as conn,
        conn.transaction(force_rollback=True),
        pytest.raises(errors.RestrictViolation),
    ):
        conn.execute(statement.encode())


def test_a_correction_is_a_new_row_once(db: PgDatabase, tenants: SeededTenants) -> None:
    a = tenants.a
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, a["tenants"])
        payment = _verified(conn, a["tenants"], a["orders"], a["staff_members"])
        _reverse(conn, a["tenants"], payment, a["staff_members"])
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _reverse(conn, a["tenants"], payment, a["staff_members"])
        # A reversal must name what it corrects and why.
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "INSERT INTO velora.payments (tenant_id, order_id, method, amount_paisa, status, "
                "verified_by_staff_id, verified_at) "
                "VALUES (%s, %s, 'transfer', 90000, 'reversed', %s, now())",
                (a["tenants"], a["orders"], a["staff_members"]),
            )


def _move(conn: psycopg.Connection[Any], order: UUID, to: str) -> None:
    conn.execute("UPDATE velora.orders SET status = %s WHERE id = %s", (to, order))


def test_an_order_is_paid_only_with_a_verified_ledger_entry(
    db: PgDatabase, tenants: SeededTenants
) -> None:
    a = tenants.a
    with db.app() as conn, conn.transaction(force_rollback=True):
        # Created inside the rolled-back transaction: the seeded tenants stay as seeded.
        order = insert_order(
            conn, a["tenants"], status="awaiting_transfer", payment_method="transfer"
        )
        _as_tenant(conn, a["tenants"])
        with (
            pytest.raises(errors.CheckViolation, match="no staff-verified payment"),
            conn.transaction(),
        ):
            _move(conn, order, "paid_staff_verified")
        payment = _verified(conn, a["tenants"], order, a["staff_members"])
        _move(conn, order, "paid_staff_verified")
        # Withdrawn: back to awaiting, and it can't be marked paid again on that entry.
        _reverse(conn, a["tenants"], payment, a["staff_members"])
        _move(conn, order, "awaiting_transfer")
        with pytest.raises(errors.CheckViolation, match="no staff-verified payment"):
            _move(conn, order, "paid_staff_verified")


def test_a_late_refund_needs_a_verified_ledger_entry(
    db: PgDatabase, tenants: SeededTenants
) -> None:
    a = tenants.a
    with db.app() as conn, conn.transaction(force_rollback=True):
        order = insert_order(conn, a["tenants"], status="expired", payment_method="transfer")
        _as_tenant(conn, a["tenants"])
        with (
            pytest.raises(errors.CheckViolation, match="no staff-verified payment"),
            conn.transaction(),
        ):
            _move(conn, order, "refund_pending")
        _verified(conn, a["tenants"], order, a["staff_members"])
        _move(conn, order, "refund_pending")
