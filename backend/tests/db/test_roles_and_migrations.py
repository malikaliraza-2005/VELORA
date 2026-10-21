"""P2-T1: roles and migration tooling."""

from __future__ import annotations

import pytest

from tests.db.conftest import APP_ROLE, OWNER_ROLE, Cluster, PgDatabase
from velora.db.migrate import (
    MigrationError,
    applied_versions,
    load_migrations,
    migrate_down,
    migrate_up,
)


def test_runtime_role_cannot_bypass_rls(db: PgDatabase) -> None:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit "
            "FROM pg_roles WHERE rolname = %s",
            (APP_ROLE,),
        ).fetchone()
    assert row == (False, False, False, False, False)


@pytest.mark.parametrize("role", [OWNER_ROLE, "velora_resolver"])
def test_other_roles_are_not_privileged(db: PgDatabase, role: str) -> None:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = %s",
            (role,),
        ).fetchone()
    assert row == (False, False, False)


def test_runtime_role_owns_nothing(db: PgDatabase) -> None:
    with db.admin() as conn:
        owned = conn.execute(
            """
            SELECT 'relation ' || oid::regclass::text FROM pg_class
             WHERE relowner = 'app_rw'::regrole
            UNION ALL
            SELECT 'function ' || oid::regprocedure::text FROM pg_proc
             WHERE proowner = 'app_rw'::regrole
            UNION ALL
            SELECT 'schema ' || nspname FROM pg_namespace WHERE nspowner = 'app_rw'::regrole
            UNION ALL
            SELECT 'type ' || oid::regtype::text FROM pg_type
             WHERE typowner = 'app_rw'::regrole
            """
        ).fetchall()
    assert owned == []


def test_migrator_owns_every_table(db: PgDatabase) -> None:
    with db.admin() as conn:
        owners = conn.execute(
            "SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE relnamespace = 'velora'::regnamespace"
        ).fetchall()
    assert owners == [(OWNER_ROLE,)]


def test_runtime_role_cannot_create_objects(db: PgDatabase) -> None:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT has_schema_privilege('app_rw', 'velora', 'CREATE'), "
            "has_schema_privilege('app_rw', 'public', 'CREATE'), "
            "has_schema_privilege('app_rw', 'velora_migrations', 'USAGE')"
        ).fetchone()
    assert row == (False, False, False)


def test_migrations_are_reversible(cluster: Cluster, empty_db: PgDatabase) -> None:
    url = cluster.url(empty_db.name, OWNER_ROLE)
    latest = load_migrations()[-1].version
    all_versions = list(range(1, latest + 1))

    assert migrate_up(url) == all_versions
    assert migrate_up(url) == []  # idempotent
    assert migrate_down(url) == list(reversed(all_versions))
    with empty_db.admin() as conn:
        assert conn.execute("SELECT to_regnamespace('velora')").fetchone() == (None,)
    assert migrate_up(url) == all_versions
    assert applied_versions(url) == all_versions


def test_edited_migration_is_refused(cluster: Cluster, empty_db: PgDatabase) -> None:
    url = cluster.url(empty_db.name, OWNER_ROLE)
    migrate_up(url, target=1)
    with empty_db.admin() as conn:
        conn.execute("UPDATE velora_migrations.applied SET checksum = 'tampered'")
    with pytest.raises(MigrationError, match="edited"):
        migrate_up(url)
