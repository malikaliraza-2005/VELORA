"""P2-T3/T6: tenant isolation.

- Enumeration: every table either has its tenant key + RLS + FORCE + the shared policy, or is
  on an explicit allowlist of global tables.
- Cross-tenant: as app_rw in tenant A's context, tenant B's rows are invisible and untouchable
  on every table.
- Fail closed: without a tenant context, nothing is visible and nothing can be written.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg import errors, sql

from tests.db.conftest import PgDatabase, SeededTenants

# Every tenant table and the column that holds its tenant. A new table must be added here,
# to the seed, and to a migration that calls enable_tenant_rls(); the enumeration test fails
# until all three agree.
TENANT_TABLES: dict[str, str] = {
    "tenants": "id",
    "channels": "tenant_id",
    "staff_members": "tenant_id",
    "staff_sessions": "tenant_id",
    "magic_links": "tenant_id",
    "consents": "tenant_id",
    "owner_verifications": "tenant_id",
    "customers": "tenant_id",
    "conversations": "tenant_id",
    "inbox_events": "tenant_id",
    "outbox_messages": "tenant_id",
    "pending_actions": "tenant_id",
    "orders": "tenant_id",
    "order_items": "tenant_id",
    "order_transitions": "tenant_id",
    "payments": "tenant_id",
    "audit_log": "tenant_id",
    "channel_events": "tenant_id",
    "menu_versions": "tenant_id",
    "menu_categories": "tenant_id",
    "menu_items": "tenant_id",
    "modifier_groups": "tenant_id",
    "modifier_options": "tenant_id",
    "menu_publications": "tenant_id",
    "menu_item_availability": "tenant_id",
    "ordering_settings": "tenant_id",
    "order_capacity_slots": "tenant_id",
}

# Tables in the velora schema that are deliberately global (no tenant). Each needs a reason.
GLOBAL_TABLES: dict[str, str] = {
    # P5: one switch that stops new orders for every tenant (AD-19). Append-only; the runtime
    # can only read it, and only the founder's tool appends to it.
    "global_pause_events": "platform-wide pause switch",
}

# Policies other than tenant_isolation: (table, policy) -> (roles, command).
EXTRA_POLICIES: dict[tuple[str, str], tuple[list[str], str]] = {
    # Bootstrap resolver lookup (P2-T4); the role is NOLOGIN and reachable only through the
    # SECURITY DEFINER resolver function.
    ("channels", "resolver_lookup"): (["velora_resolver"], "SELECT"),
    # Worker work discovery (P5): tenant ids and queue-timing columns only (column grants,
    # checked in tests/messaging/test_dispatch.py).
    ("channels", "dispatch_lookup"): (["velora_dispatcher"], "SELECT"),
    ("inbox_events", "dispatch_lookup"): (["velora_dispatcher"], "SELECT"),
    ("outbox_messages", "dispatch_lookup"): (["velora_dispatcher"], "SELECT"),
    # P7: which tenants have a transfer window due (tenant_id, status, payment_due_at only).
    ("orders", "dispatch_lookup"): (["velora_dispatcher"], "SELECT"),
    # Founder onboarding tools (P3): the platform role may list tenants, and otherwise works
    # one tenant at a time under the shared tenant expression (checked below).
    ("tenants", "platform_directory"): (["velora_platform"], "SELECT"),
    **{
        (table, "platform_scope"): (["velora_platform"], "ALL")
        for table in (
            "tenants",
            "channels",
            "staff_members",
            "staff_sessions",
            "magic_links",
            "consents",
            "owner_verifications",
            "audit_log",
            # P4: the founder's menu import fills a draft (but can't publish it).
            "menu_versions",
            "menu_categories",
            "menu_items",
            "modifier_groups",
            "modifier_options",
            "menu_publications",
        )
    },
}

APPEND_ONLY = {
    "consents",
    "owner_verifications",
    "order_transitions",
    # P7: an order's lines are its price snapshot.
    "order_items",
    "audit_log",
    "channel_events",
    "menu_publications",
    "global_pause_events",
}

# Saved as a whole (delete and re-insert), never updated in place: the menu draft content.
REPLACED_WHOLE = {"menu_categories", "menu_items", "modifier_groups", "modifier_options"}

tables = pytest.mark.parametrize("table", sorted(TENANT_TABLES))


def _as_tenant(conn: psycopg.Connection[Any], tenant: UUID | str) -> None:
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))


def _ident(table: str) -> sql.Composed:
    return sql.SQL("velora.{}").format(sql.Identifier(table))


def _updatable_column(conn: psycopg.Connection[Any], table: str) -> str | None:
    row = conn.execute(
        """
        SELECT a.attname FROM pg_attribute a
         WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
           AND a.attname NOT IN ('id', 'tenant_id')
           AND has_column_privilege('app_rw', a.attrelid, a.attname, 'UPDATE')
         ORDER BY a.attnum LIMIT 1
        """,
        (f"velora.{table}",),
    ).fetchone()
    return None if row is None else str(row[0])


# Enumeration ------------------------------------------------------------------------------


def test_every_table_is_classified(db: PgDatabase) -> None:
    with db.admin() as conn:
        found = {
            r[0]
            for r in conn.execute(
                "SELECT relname FROM pg_class WHERE relnamespace = 'velora'::regnamespace "
                "AND relkind IN ('r', 'p', 'v', 'm', 'f')"
            ).fetchall()
        }
    assert found == set(TENANT_TABLES) | set(GLOBAL_TABLES)


def test_no_tables_outside_the_velora_schemas(db: PgDatabase) -> None:
    with db.admin() as conn:
        stray = conn.execute(
            """
            SELECT n.nspname || '.' || c.relname FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
               AND n.nspname NOT IN ('velora', 'velora_migrations', 'pg_catalog',
                                     'information_schema', 'pg_toast')
            """
        ).fetchall()
    assert stray == []


@tables
def test_table_has_forced_rls_and_the_shared_policy(db: PgDatabase, table: str) -> None:
    key = TENANT_TABLES[table]
    with db.admin() as conn:
        flags = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"velora.{table}",),
        ).fetchone()
        key_col = conn.execute(
            "SELECT format_type(atttypid, atttypmod), attnotnull FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attname = %s",
            (f"velora.{table}", key),
        ).fetchone()
        policies = conn.execute(
            "SELECT policyname, permissive, roles::text[], cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname = 'velora' AND tablename = %s",
            (table,),
        ).fetchall()

    assert flags == (True, True), "RLS must be enabled and FORCED"
    assert key_col == ("uuid", True)
    expected = f"({key} = velora.current_tenant_id())"
    by_name = {p[0]: p[1:] for p in policies}
    assert by_name.pop("tenant_isolation") == ("PERMISSIVE", ["app_rw"], "ALL", expected, expected)
    for name, (_, roles, cmd, qual, with_check) in by_name.items():
        assert EXTRA_POLICIES.get((table, name)) == (roles, cmd), f"unexpected policy {name}"
        if name == "platform_scope":
            assert (qual, with_check) == (expected, expected)


def test_runtime_role_has_no_access_to_the_migration_ledger(db: PgDatabase) -> None:
    with db.app() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM velora_migrations.applied")


@pytest.mark.parametrize("table", sorted(APPEND_ONLY))
def test_history_tables_are_append_only(db: PgDatabase, table: str) -> None:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT has_table_privilege('app_rw', %s, 'UPDATE'), "
            "has_table_privilege('app_rw', %s, 'DELETE'), "
            "has_table_privilege('app_rw', %s, 'TRUNCATE')",
            (f"velora.{table}",) * 3,
        ).fetchone()
    assert row == (False, False, False)


def test_seed_covers_every_table(tenants: SeededTenants) -> None:
    assert set(tenants.a) == set(TENANT_TABLES)


# Cross-tenant -----------------------------------------------------------------------------


@tables
def test_cross_tenant_read_sees_only_own_rows(
    db: PgDatabase, tenants: SeededTenants, table: str
) -> None:
    key = TENANT_TABLES[table]
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        other = conn.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE id = %s").format(_ident(table)),
            (tenants.b[table],),
        ).fetchone()
        owners = conn.execute(
            sql.SQL("SELECT DISTINCT {} FROM {}").format(sql.Identifier(key), _ident(table))
        ).fetchall()
    assert other == (0,)
    assert owners == [(tenants.a["tenants"],)]


@tables
def test_cross_tenant_update_affects_zero_rows(
    db: PgDatabase, tenants: SeededTenants, table: str
) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        column = _updatable_column(conn, table)
        if column is None:
            assert table in APPEND_ONLY | REPLACED_WHOLE
            return
        _as_tenant(conn, tenants.a["tenants"])
        query = sql.SQL("UPDATE {t} SET {c} = {c} WHERE id = %s").format(
            t=_ident(table), c=sql.Identifier(column)
        )
        assert conn.execute(query, (tenants.b[table],)).rowcount == 0
        # An unfiltered update reaches tenant A's rows only.
        unfiltered = sql.SQL("UPDATE {t} SET {c} = {c}").format(
            t=_ident(table), c=sql.Identifier(column)
        )
        assert conn.execute(unfiltered).rowcount == 1


@tables
def test_cross_tenant_delete_affects_zero_rows(
    db: PgDatabase, tenants: SeededTenants, table: str
) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        can_delete = conn.execute(
            "SELECT has_table_privilege('app_rw', %s, 'DELETE')", (f"velora.{table}",)
        ).fetchone()
        if can_delete == (False,):
            return  # no DELETE grant at all, which is stronger
        _as_tenant(conn, tenants.a["tenants"])
        query = sql.SQL("DELETE FROM {} WHERE id = %s").format(_ident(table))
        assert conn.execute(query, (tenants.b[table],)).rowcount == 0


@tables
def test_cannot_write_rows_for_another_tenant(
    db: PgDatabase, tenants: SeededTenants, table: str
) -> None:
    """Moving one of A's rows to B is rejected by the policy's WITH CHECK."""
    key = TENANT_TABLES[table]
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        query = sql.SQL("UPDATE {} SET {k} = %s WHERE id = %s").format(
            _ident(table), k=sql.Identifier(key)
        )
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(query, (tenants.b["tenants"], tenants.a[table]))


def test_cannot_insert_into_another_tenant(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
            conn.execute(
                "INSERT INTO velora.customers (tenant_id, phone_e164) VALUES (%s, '+923000000077')",
                (tenants.b["tenants"],),
            )


# Fail closed ------------------------------------------------------------------------------


@pytest.mark.parametrize("context", [None, ""], ids=["unset", "empty"])
@tables
def test_no_tenant_context_sees_nothing(
    db: PgDatabase, tenants: SeededTenants, table: str, context: str | None
) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        if context is not None:
            _as_tenant(conn, context)
        count = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(_ident(table))).fetchone()
        column = _updatable_column(conn, table)
        if column is not None:
            updated = conn.execute(
                sql.SQL("UPDATE {t} SET {c} = {c}").format(
                    t=_ident(table), c=sql.Identifier(column)
                )
            ).rowcount
            assert updated == 0
    assert count == (0,)


def test_no_tenant_context_cannot_insert(db: PgDatabase, tenants: SeededTenants) -> None:
    with (
        db.app() as conn,
        conn.transaction(force_rollback=True),
        pytest.raises(errors.InsufficientPrivilege, match="row-level security"),
    ):
        conn.execute(
            "INSERT INTO velora.customers (tenant_id, phone_e164) VALUES (%s, '+923000000078')",
            (tenants.a["tenants"],),
        )


def test_malformed_tenant_context_is_an_error(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, "not-a-uuid")
        with pytest.raises(errors.InvalidTextRepresentation):
            conn.execute("SELECT count(*) FROM velora.orders")


def test_tenant_context_does_not_outlive_the_transaction(
    db: PgDatabase, tenants: SeededTenants
) -> None:
    with db.app() as conn:
        with conn.transaction():
            _as_tenant(conn, tenants.a["tenants"])
            inside = conn.execute("SELECT count(*) FROM velora.orders").fetchone()
        after = conn.execute("SELECT count(*) FROM velora.orders").fetchone()
    assert inside == (1,)
    assert after == (0,)
