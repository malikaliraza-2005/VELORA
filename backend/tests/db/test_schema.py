"""P2-T2: canonical core schema."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.db.conftest import PgDatabase, SeededTenants


def _as_tenant(conn: psycopg.Connection[Any], tenant: UUID) -> None:
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))


def test_no_floating_point_columns(db: PgDatabase) -> None:
    """Schema lint: money must never be stored as float, real, double or `money`."""
    with db.admin() as conn:
        bad = conn.execute(
            """
            SELECT c.relname || '.' || a.attname || ' ' || format_type(a.atttypid, a.atttypmod)
              FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
             WHERE c.relnamespace = 'velora'::regnamespace AND a.attnum > 0
               AND NOT a.attisdropped
               AND a.atttypid IN ('real'::regtype, 'double precision'::regtype, 'money'::regtype)
            """
        ).fetchall()
    assert bad == []


def test_money_columns_are_integer_paisa(db: PgDatabase) -> None:
    with db.admin() as conn:
        rows = conn.execute(
            """
            SELECT c.relname || '.' || a.attname, format_type(a.atttypid, a.atttypmod)
              FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
             WHERE c.relnamespace = 'velora'::regnamespace AND c.relkind = 'r'
               AND a.attname LIKE '%%paisa'
            """
        ).fetchall()
    assert rows, "expected paisa columns"
    assert {t for _, t in rows} == {"bigint"}, rows


def test_every_foreign_key_restricts_deletes(db: PgDatabase) -> None:
    """Covers the financial foreign keys (orders, items, payments, customers) and the rest."""
    with db.admin() as conn:
        rows = conn.execute(
            """
            SELECT conrelid::regclass::text, conname, confdeltype
              FROM pg_constraint
             WHERE contype = 'f' AND connamespace = 'velora'::regnamespace
            """
        ).fetchall()
    assert len(rows) > 10
    assert [r for r in rows if r[2] != "r"] == []


def test_same_order_number_in_two_tenants_but_not_twice_in_one(
    db: PgDatabase, tenants: SeededTenants
) -> None:
    """Per-tenant composite key (tenant_id, order_number). The (tenant_id, sku) key belongs
    to the menu tables and is tested in P4."""
    with db.app() as conn, conn.transaction(force_rollback=True):
        for side in (tenants.a, tenants.b):
            _as_tenant(conn, side["tenants"])
            n = conn.execute("SELECT velora.next_order_number()").fetchone()
            assert n is not None
            assert n[0] == 2  # each tenant's own sequence; order 1 was seeded
        _as_tenant(conn, tenants.a["tenants"])
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            conn.execute(
                "INSERT INTO velora.orders (tenant_id, order_number, customer_id, fulfilment) "
                "VALUES (%s, 1, %s, 'takeaway')",
                (tenants.a["tenants"], tenants.a["customers"]),
            )


def test_order_numbers_do_not_collide_across_tenants(
    db: PgDatabase, tenants: SeededTenants
) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        for side in (tenants.a, tenants.b):
            _as_tenant(conn, side["tenants"])
            conn.execute(
                "INSERT INTO velora.orders (tenant_id, order_number, customer_id, fulfilment) "
                "VALUES (%s, 7, %s, 'takeaway')",
                (side["tenants"], side["customers"]),
            )


def test_next_order_number_without_tenant_is_null(db: PgDatabase) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        assert conn.execute("SELECT velora.next_order_number()").fetchone() == (None,)


def test_child_cannot_reference_another_tenants_row(db: PgDatabase, tenants: SeededTenants) -> None:
    """Composite foreign keys stop tenant A's item pointing at tenant B's order."""
    with (
        db.admin() as conn,
        conn.transaction(force_rollback=True),
        pytest.raises(errors.ForeignKeyViolation),
    ):
        conn.execute(
            "INSERT INTO velora.order_items (tenant_id, order_id, line_no, sku, "
            "name_snapshot, unit_price_paisa, quantity, line_total_paisa) "
            "VALUES (%s, %s, 9, 'X', 'X', 100, 1, 100)",
            (tenants.a["tenants"], tenants.b["orders"]),
        )


def test_deleting_a_customer_with_orders_is_blocked(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute("DELETE FROM velora.customers WHERE id = %s", (tenants.a["customers"],))


def test_customer_without_history_can_be_deleted(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        row = conn.execute(
            "INSERT INTO velora.customers (tenant_id, phone_e164) "
            "VALUES (%s, '+923000000099') RETURNING id",
            (tenants.a["tenants"],),
        ).fetchone()
        assert row is not None
        cur = conn.execute("DELETE FROM velora.customers WHERE id = %s", (row[0],))
        assert cur.rowcount == 1


def test_anonymisation(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        customer = tenants.a["customers"]
        result = conn.execute("SELECT velora.anonymise_customer(%s)", (customer,)).fetchone()
        assert result == (True,)
        row = conn.execute(
            "SELECT phone_e164, display_name, deleted_at IS NOT NULL, anonymised_at IS NOT NULL "
            "FROM velora.customers WHERE id = %s",
            (customer,),
        ).fetchone()
        assert row == (None, None, True, True)
        address = conn.execute(
            "SELECT delivery_address, delivery_note FROM velora.orders WHERE customer_id = %s",
            (customer,),
        ).fetchall()
        assert address == [(None, None)]
        # Financial history is kept.
        count = conn.execute(
            "SELECT count(*) FROM velora.orders WHERE customer_id = %s", (customer,)
        ).fetchone()
        assert count == (1,)


def test_anonymisation_cannot_reach_another_tenant(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        result = conn.execute(
            "SELECT velora.anonymise_customer(%s)", (tenants.b["customers"],)
        ).fetchone()
        assert result == (False,)
    with db.admin() as conn:
        phone = conn.execute(
            "SELECT phone_e164 FROM velora.customers WHERE id = %s", (tenants.b["customers"],)
        ).fetchone()
    assert phone == ("+923000000001",)


@pytest.mark.parametrize(
    ("status", "staff", "reference", "ok"),
    [
        ("verified", False, "TXN123", False),  # no staff member
        ("verified", True, None, False),  # transfer without a reference
        ("verified", True, "TXN123", True),
        ("customer_claimed", False, None, True),
    ],
)
def test_transfer_verification_needs_staff_and_reference(
    db: PgDatabase,
    tenants: SeededTenants,
    status: str,
    staff: bool,
    reference: str | None,
    ok: bool,
) -> None:
    a = tenants.a
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, a["tenants"])
        query = (
            "INSERT INTO velora.payments (tenant_id, order_id, method, amount_paisa, status, "
            "reference, verified_by_staff_id, verified_at) "
            "VALUES (%s, %s, 'transfer', 105000, %s, %s, %s, "
            "CASE WHEN %s THEN now() END)"
        )
        params = (
            a["tenants"],
            a["orders"],
            status,
            reference,
            a["staff_members"] if staff else None,
            staff,
        )
        if ok:
            conn.execute(query, params)
        else:
            with pytest.raises(errors.CheckViolation):
                conn.execute(query, params)


def test_order_total_must_add_up(db: PgDatabase, tenants: SeededTenants) -> None:
    a = tenants.a
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, a["tenants"])
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "UPDATE velora.orders SET total_paisa = total_paisa + 1 WHERE id = %s",
                (a["orders"],),
            )
