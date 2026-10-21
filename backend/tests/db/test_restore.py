"""P2-T6: restore compatibility. A dump and restore keeps roles, grants and policies.

Also used by P11. Database-level privileges (CONNECT) are not part of a plain `pg_dump`;
they need `pg_dump --create` or a cluster dump, which P11 covers.
"""

from __future__ import annotations

import subprocess
from typing import Any

import psycopg

from tests.db.conftest import Cluster, PgDatabase, new_database_name

# Everything that defines who can do what, for the velora schemas.
SNAPSHOT_QUERIES = {
    "schemas": """
        SELECT nspname, pg_get_userbyid(nspowner), coalesce(nspacl::text[], '{}')
          FROM pg_namespace WHERE nspname LIKE 'velora%' ORDER BY 1
    """,
    "relations": """
        SELECT c.oid::regclass::text, c.relkind, pg_get_userbyid(c.relowner),
               c.relrowsecurity, c.relforcerowsecurity, coalesce(c.relacl::text[], '{}')
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname LIKE 'velora%' ORDER BY 1
    """,
    "column_grants": """
        SELECT c.oid::regclass::text, a.attname, a.attacl::text[]
          FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname LIKE 'velora%' AND a.attacl IS NOT NULL ORDER BY 1, 2
    """,
    "policies": """
        SELECT schemaname, tablename, policyname, permissive, roles::text[], cmd, qual,
               with_check
          FROM pg_policies WHERE schemaname LIKE 'velora%' ORDER BY 1, 2, 3
    """,
    "functions": """
        SELECT p.oid::regprocedure::text, pg_get_userbyid(p.proowner), p.prosecdef,
               coalesce(p.proconfig, '{}'), coalesce(p.proacl::text[], '{}')
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname LIKE 'velora%' ORDER BY 1
    """,
    "constraints": """
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
          FROM pg_constraint WHERE connamespace = 'velora'::regnamespace ORDER BY 1, 2
    """,
}


def _snapshot(conn: psycopg.Connection[Any]) -> dict[str, list[tuple[Any, ...]]]:
    return {
        name: conn.execute(query.encode()).fetchall() for name, query in SNAPSHOT_QUERIES.items()
    }


def _run(cluster: Cluster, cmd: list[str], stdin: bytes | None = None) -> bytes:
    result = subprocess.run(  # noqa: S603 - argv built from test configuration, no shell
        cmd, input=stdin, capture_output=True, env=cluster.tool_env(), check=False
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


def test_dump_and_restore_keeps_grants_and_policies(cluster: Cluster, db: PgDatabase) -> None:
    dump = _run(cluster, cluster.tool_command("pg_dump", "--format=custom", dbname=db.name))

    restored = new_database_name("restored")
    cluster.create_database(restored)
    try:
        _run(
            cluster,
            cluster.tool_command("pg_restore", "--exit-on-error", "--no-comments", dbname=restored),
            stdin=dump,
        )
        with db.admin() as original_conn, psycopg.connect(cluster.url(restored)) as restored_conn:
            original = _snapshot(original_conn)
            copy = _snapshot(restored_conn)
            assert copy == original

            # The restored copy still isolates tenants for the runtime role.
            restored_conn.execute("SET ROLE app_rw")
            rows = restored_conn.execute("SELECT count(*) FROM velora.orders").fetchone()
            assert rows == (0,)
    finally:
        cluster.drop_database(restored)


def test_role_dump_keeps_role_attributes(cluster: Cluster) -> None:
    out = _run(cluster, cluster.tool_command("pg_dumpall", "--roles-only")).decode()
    lines = {line.strip() for line in out.splitlines()}

    def role_line(role: str) -> str:
        found = [ln for ln in lines if ln.startswith(f"ALTER ROLE {role} WITH")]
        assert len(found) == 1, f"no attribute line for {role}"
        return found[0]

    assert "NOBYPASSRLS" in role_line("app_rw")
    assert "NOSUPERUSER" in role_line("app_rw")
    assert "NOBYPASSRLS" in role_line("velora_owner")
    assert "NOLOGIN" in role_line("velora_resolver")
    assert "ALTER ROLE app_rw SET search_path TO 'velora';" in lines
    # SET TRUE is the default, so pg_dumpall prints only the non-default INHERIT FALSE.
    grants = [ln for ln in lines if ln.startswith("GRANT velora_resolver TO velora_owner")]
    assert len(grants) == 1
    assert "WITH INHERIT FALSE" in grants[0]
    assert "SET FALSE" not in grants[0]
