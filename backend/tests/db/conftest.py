"""PostgreSQL fixtures for the database tests (P2 onwards).

Environment:
  VELORA_TEST_ADMIN_URL    superuser URL of a disposable PostgreSQL 17 server. Without it the
                           database tests are skipped, unless VELORA_REQUIRE_DB_TESTS=1 (as in
                           CI), in which case they fail.
  VELORA_TEST_PG_TOOLS     optional command prefix for pg_dump/pg_restore/pg_dumpall, e.g.
                           "docker exec -i velora-pg". With a prefix the tools connect over
                           the container's local socket as the admin user.

Each test session creates the cluster roles, gives them random passwords, and migrates a
fresh, uniquely named database, which is dropped afterwards.
"""

from __future__ import annotations

import functools
import os
import secrets
import shlex
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from tests.db.seed import seed_tenant
from velora.db.migrate import migrate_up, setup_roles

APP_ROLE = "app_rw"
OWNER_ROLE = "velora_owner"
PLATFORM_ROLE = "velora_platform"
DISPATCHER_ROLE = "velora_dispatcher"
MONITOR_ROLE = "velora_monitor"
BACKUP_ROLE = "velora_backup"


@dataclass(frozen=True)
class Cluster:
    admin_url: str
    tools: list[str]
    passwords: dict[str, str]

    def url(self, dbname: str, role: str | None = None) -> str:
        if role is None:
            return make_conninfo(self.admin_url, dbname=dbname)
        return make_conninfo(
            self.admin_url, dbname=dbname, user=role, password=self.passwords[role]
        )

    def create_database(self, name: str) -> None:
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(OWNER_ROLE)
                )
            )

    def drop_database(self, name: str) -> None:
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )

    def tool_command(self, tool: str, *args: str, dbname: str | None = None) -> list[str]:
        params: dict[str, Any] = conninfo_to_dict(self.admin_url)
        cmd = [*self.tools, tool, "-U", str(params.get("user", "postgres"))]
        if not self.tools:
            cmd += ["-h", str(params.get("host", "localhost")), "-p", str(params.get("port", 5432))]
        if dbname is not None:
            cmd += ["-d", dbname]
        return cmd + list(args)

    def tool_env(self) -> dict[str, str]:
        env = dict(os.environ)
        password = conninfo_to_dict(self.admin_url).get("password")
        if password and not self.tools:
            env["PGPASSWORD"] = str(password)
        return env


@dataclass(frozen=True)
class PgDatabase:
    cluster: Cluster
    name: str

    def admin(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.cluster.url(self.name))

    def owner(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.cluster.url(self.name, OWNER_ROLE))

    def app(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.cluster.url(self.name, APP_ROLE))

    def platform(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.cluster.url(self.name, PLATFORM_ROLE))

    def dispatcher(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.cluster.url(self.name, DISPATCHER_ROLE))

    def monitor(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.cluster.url(self.name, MONITOR_ROLE))

    def backup(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.cluster.url(self.name, BACKUP_ROLE))


@pytest.fixture(scope="session")
def cluster() -> Cluster:
    admin_url = os.environ.get("VELORA_TEST_ADMIN_URL", "").strip()
    if not admin_url:
        if os.environ.get("VELORA_REQUIRE_DB_TESTS") == "1":
            pytest.fail("VELORA_REQUIRE_DB_TESTS=1 but VELORA_TEST_ADMIN_URL is not set")
        pytest.skip("VELORA_TEST_ADMIN_URL not set; database tests skipped")
    return _prepare_cluster(admin_url)


@functools.cache
def _prepare_cluster(admin_url: str) -> Cluster:
    """Once per test process, even when another conftest re-exports these fixtures: the role
    passwords are cluster-wide, so setting them twice would break the first Cluster."""
    setup_roles(admin_url)
    passwords = {
        role: secrets.token_urlsafe(24)
        for role in (
            OWNER_ROLE,
            APP_ROLE,
            PLATFORM_ROLE,
            DISPATCHER_ROLE,
            MONITOR_ROLE,
            BACKUP_ROLE,
        )
    }
    with psycopg.connect(admin_url, autocommit=True) as conn:
        for role, password in passwords.items():
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
    tools = shlex.split(os.environ.get("VELORA_TEST_PG_TOOLS", ""))
    return Cluster(admin_url=admin_url, tools=tools, passwords=passwords)


def new_database_name(label: str) -> str:
    return f"velora_test_{label}_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def db(cluster: Cluster) -> Iterator[PgDatabase]:
    name = new_database_name("main")
    cluster.create_database(name)
    try:
        migrate_up(cluster.url(name, OWNER_ROLE))
        yield PgDatabase(cluster, name)
    finally:
        cluster.drop_database(name)


@pytest.fixture()
def empty_db(cluster: Cluster) -> Iterator[PgDatabase]:
    """A fresh, unmigrated database owned by the migrator."""
    name = new_database_name("scratch")
    cluster.create_database(name)
    try:
        yield PgDatabase(cluster, name)
    finally:
        cluster.drop_database(name)


@dataclass(frozen=True)
class SeededTenants:
    a: dict[str, uuid.UUID]
    b: dict[str, uuid.UUID]


@pytest.fixture(scope="session")
def tenants(db: PgDatabase) -> SeededTenants:
    """Two fully seeded tenants, A and B, shared by the session. Tests must not mutate them
    except inside a transaction they roll back."""
    with db.admin() as conn:
        return SeededTenants(
            a=seed_tenant(conn, "tenant-a", "TA"), b=seed_tenant(conn, "tenant-b", "TB")
        )
