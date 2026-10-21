"""P2-T4: bootstrap resolver (provider session reference -> tenant)."""

from __future__ import annotations

import pytest
from psycopg import errors

from tests.db.conftest import PgDatabase, SeededTenants

RESOLVER = "velora.resolve_channel_tenant(text,text)"


def test_resolves_session_to_tenant_without_context(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn:
        a = conn.execute(
            "SELECT velora.resolve_channel_tenant('fake', 'session-tenant-a')"
        ).fetchone()
        b = conn.execute(
            "SELECT velora.resolve_channel_tenant('fake', 'session-tenant-b')"
        ).fetchone()
        unknown = conn.execute(
            "SELECT velora.resolve_channel_tenant('fake', 'no-such-session')"
        ).fetchone()
        wrong_provider = conn.execute(
            "SELECT velora.resolve_channel_tenant('openwa', 'session-tenant-a')"
        ).fetchone()
    assert a == (tenants.a["tenants"],)
    assert b == (tenants.b["tenants"],)
    assert unknown == (None,)
    assert wrong_provider == (None,)


def test_retired_channel_does_not_resolve(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.admin() as conn, conn.transaction(force_rollback=True):
        conn.execute(
            "UPDATE velora.channels SET state = 'retired' WHERE id = %s", (tenants.a["channels"],)
        )
        conn.execute("SET LOCAL ROLE app_rw")
        row = conn.execute(
            "SELECT velora.resolve_channel_tenant('fake', 'session-tenant-a')"
        ).fetchone()
    assert row == (None,)


def test_resolver_returns_only_a_tenant_id(db: PgDatabase) -> None:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT prorettype::regtype::text, proretset, prosecdef, proconfig, "
            "pg_get_userbyid(proowner) FROM pg_proc WHERE oid = %s::regprocedure",
            (RESOLVER,),
        ).fetchone()
    assert row == ("uuid", False, True, ["search_path=pg_catalog, pg_temp"], "velora_resolver")


def test_public_cannot_execute_the_resolver(db: PgDatabase) -> None:
    with db.admin() as conn:
        acl = conn.execute(
            "SELECT grantee::regrole::text, privilege_type FROM pg_proc, "
            "aclexplode(proacl) WHERE oid = %s::regprocedure",
            (RESOLVER,),
        ).fetchall()
    grantees = {(g if g != "-" else "PUBLIC", p) for g, p in acl}
    assert ("PUBLIC", "EXECUTE") not in grantees
    assert ("0", "EXECUTE") not in grantees
    assert ("app_rw", "EXECUTE") in grantees
    # Only the owner and app_rw hold EXECUTE.
    assert {g for g, _ in grantees} == {"velora_resolver", "app_rw"}


def test_no_other_security_definer_function_exists(db: PgDatabase) -> None:
    with db.admin() as conn:
        rows = conn.execute(
            """
            SELECT p.oid::regprocedure::text FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE p.prosecdef
               AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            """
        ).fetchall()
    assert rows == [("velora.resolve_channel_tenant(text,text)",)]


def test_resolver_role_sees_only_lookup_columns(db: PgDatabase) -> None:
    with db.admin() as conn:
        granted = conn.execute(
            """
            SELECT table_name, column_name, privilege_type
              FROM information_schema.column_privileges
             WHERE grantee = 'velora_resolver' AND table_schema = 'velora'
            """
        ).fetchall()
        tables = conn.execute(
            "SELECT count(*) FROM information_schema.table_privileges "
            "WHERE grantee = 'velora_resolver'"
        ).fetchone()
    assert {(t, c, p) for t, c, p in granted} == {
        ("channels", c, "SELECT") for c in ("tenant_id", "provider", "session_ref", "state")
    }
    assert tables == (0,)


def test_runtime_role_cannot_use_the_resolver_policy(
    db: PgDatabase, tenants: SeededTenants
) -> None:
    """app_rw is not a member of velora_resolver, so it cannot borrow its policy."""
    with (
        db.app() as conn,
        conn.transaction(force_rollback=True),
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute("SET LOCAL ROLE velora_resolver")
