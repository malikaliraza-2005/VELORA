"""Migration runner (P2-T1).

Migrations are plain SQL files in `velora/db/migrations`, named `NNNN_name.up.sql` with a
matching `NNNN_name.down.sql`. Each runs in its own transaction as the migrator role
(`velora_owner`), never as the runtime role. Applied versions and their checksums are
recorded in `velora_migrations.applied`; editing an applied migration is refused.

Down migrations exist for development only. Once data exists, fix forward.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources

import psycopg
from psycopg import sql as pgsql

_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.(?P<direction>up|down)\.sql$")

# Serialises concurrent migration runs on the same database.
_LOCK_KEY = 0x76656C6F7261  # "velora"


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up_sql: str
    down_sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.up_sql.encode("utf-8")).hexdigest()


def load_migrations() -> list[Migration]:
    files: dict[tuple[int, str], dict[str, str]] = {}
    for entry in resources.files("velora.db.migrations").iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _FILENAME.match(entry.name)
        if match is None:
            raise MigrationError(f"badly named migration file: {entry.name}")
        key = (int(match["version"]), match["name"])
        files.setdefault(key, {})[match["direction"]] = entry.read_text(encoding="utf-8")

    migrations: list[Migration] = []
    for (version, name), sql in sorted(files.items()):
        if set(sql) != {"up", "down"}:
            raise MigrationError(f"migration {version:04d}_{name} needs both up and down files")
        migrations.append(Migration(version, name, sql["up"], sql["down"]))

    versions = [m.version for m in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"migration versions must be 1..n without gaps, got {versions}")
    return migrations


def _prepare(conn: psycopg.Connection) -> dict[int, str]:
    """Create the ledger if needed, take the lock, and return applied version -> checksum."""
    conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
    conn.execute(
        """
        CREATE SCHEMA IF NOT EXISTS velora_migrations;
        CREATE TABLE IF NOT EXISTS velora_migrations.applied (
            version    integer PRIMARY KEY,
            name       text NOT NULL,
            checksum   text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    rows = conn.execute("SELECT version, checksum FROM velora_migrations.applied").fetchall()
    return {version: checksum for version, checksum in rows}


def _run(conn: psycopg.Connection, sql: str) -> None:
    conn.execute("SET search_path = velora")
    conn.execute(sql.encode("utf-8"))
    conn.execute("RESET search_path")


def applied_versions(conninfo: str) -> list[int]:
    with psycopg.connect(conninfo) as conn, conn.transaction():
        return sorted(_prepare(conn))


def migrate_up(conninfo: str, target: int | None = None) -> list[int]:
    """Apply pending migrations up to `target` (default: latest). Returns versions applied."""
    migrations = load_migrations()
    target = migrations[-1].version if target is None else target
    done: list[int] = []
    with psycopg.connect(conninfo) as conn:
        for migration in migrations:
            if migration.version > target:
                break
            with conn.transaction():
                applied = _prepare(conn)
                if migration.version in applied:
                    if applied[migration.version] != migration.checksum:
                        raise MigrationError(
                            f"migration {migration.version:04d}_{migration.name} was edited "
                            "after it was applied; add a new migration instead"
                        )
                    continue
                _run(conn, migration.up_sql)
                conn.execute(
                    "INSERT INTO velora_migrations.applied (version, name, checksum) "
                    "VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
                done.append(migration.version)
    return done


def migrate_down(conninfo: str, target: int = 0) -> list[int]:
    """Revert applied migrations above `target` (default: all). Development only."""
    migrations = load_migrations()
    done: list[int] = []
    with psycopg.connect(conninfo) as conn:
        for migration in reversed(migrations):
            if migration.version <= target:
                break
            with conn.transaction():
                if migration.version not in _prepare(conn):
                    continue
                _run(conn, migration.down_sql)
                conn.execute(
                    "DELETE FROM velora_migrations.applied WHERE version = %s",
                    (migration.version,),
                )
                done.append(migration.version)
    return done


def setup_roles(conninfo: str) -> None:
    """Create or re-assert the cluster roles. Needs an administrator connection."""
    sql = resources.files("velora.db.sql").joinpath("roles.sql").read_text(encoding="utf-8")
    with psycopg.connect(conninfo) as conn, conn.transaction():
        conn.execute(sql.encode("utf-8"))


# Login roles whose passwords `provision` sets, and the connection setting that carries each.
ROLE_URL_SETTINGS = {
    "velora_owner": "VELORA_MIGRATION_DATABASE_URL",
    "app_rw": "VELORA_DATABASE_URL",
    "velora_platform": "VELORA_PLATFORM_DATABASE_URL",
    "velora_dispatcher": "VELORA_DISPATCHER_DATABASE_URL",
    "velora_monitor": "VELORA_MONITOR_DATABASE_URL",
    "velora_backup": "VELORA_BACKUP_DATABASE_URL",
}


def provision(admin_url: str, database: str, passwords: Mapping[str, str]) -> bool:
    """Prepare a server for a deployment (P12): the roles, their passwords and the database.

    Idempotent. Re-run it after changing a password in the deployment's settings: it sets
    each given role's password again. Returns True if the database was created. Migrations
    are a separate step (`migrate up`).
    """
    unknown = set(passwords) - set(ROLE_URL_SETTINGS)
    if unknown:
        raise MigrationError(f"not a VELORA login role: {sorted(unknown)}")
    setup_roles(admin_url)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        for role, password in sorted(passwords.items()):
            conn.execute(
                pgsql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    pgsql.Identifier(role), pgsql.Literal(password)
                )
            )
        exists = conn.execute(
            "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (database,)
        )
        if exists.fetchone() is not None:
            return False
        conn.execute(
            pgsql.SQL("CREATE DATABASE {} OWNER velora_owner").format(pgsql.Identifier(database))
        )
    return True
