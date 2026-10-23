"""Restore and restore verification (P11-T3, AD-21).

`restore_backup` restores one backup into a **new** database, then checks it:

1. the manifest decrypts (so it is ours and unaltered), and each file matches its SHA-256;
2. the cluster roles are created or re-asserted from the code (`setup-roles`), and each
   VELORA role's attributes match the backed-up ones (no role gained BYPASSRLS, etc.);
3. the database is created, owned by the migrator, and `pg_restore` fills it, decrypting
   straight into pg_restore's input (no plaintext on disk);
4. the database's own grants (CONNECT), which a plain dump doesn't carry, are re-applied
   from the manifest;
5. `verify_restored` checks the copy (below). Any failed check fails the restore.

`verify_restored` checks: the schema version; every row count against the dump's snapshot;
every table that had FORCE RLS still has it, with the same policies; the VELORA roles can't
bypass RLS (except the backup role) and none is a superuser; the database grants; and
**tenant isolation**, as the runtime role: with no tenant set it sees nothing, and scoped to
each tenant it sees exactly that tenant's rows, in every tenant table.

Placing a test order on the restored copy is part of the drill (runbook), not of this code.
Role passwords aren't backed up: set them again after a restore.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from psycopg import sql
from psycopg.conninfo import make_conninfo

from velora.backup.config import PgTools
from velora.backup.crypto import BackupCryptoError, decrypt_bytes, decrypt_stream
from velora.backup.job import DATABASE, FORMAT, MANIFEST, ROLES, describe
from velora.backup.storage import Storage
from velora.db.migrate import load_migrations, setup_roles
from velora.redaction import error_text

_DBNAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_ROLE_FLAGS = {
    "SUPERUSER": "rolsuper",
    "INHERIT": "rolinherit",
    "CREATEROLE": "rolcreaterole",
    "CREATEDB": "rolcreatedb",
    "LOGIN": "rolcanlogin",
    "REPLICATION": "rolreplication",
    "BYPASSRLS": "rolbypassrls",
}
_PRIVILEGES = {"C": "CREATE", "T": "TEMPORARY", "c": "CONNECT"}
VELORA_ROLES = (
    "velora_owner",
    "app_rw",
    "velora_resolver",
    "velora_platform",
    "velora_dispatcher",
    "velora_monitor",
    "velora_backup",
)
# Only the backup role may bypass RLS (roles.sql).
BYPASS_ALLOWED = frozenset({"velora_backup"})


class RestoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class RestoreReport:
    stamp: str
    database: str
    backup_created_at: datetime
    restored_at: datetime
    duration: timedelta
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def data_age(self) -> timedelta:
        """How old the restored data was when the restore finished: the data loss if this
        backup had been restored after a real failure at `restored_at` (RPO)."""
        return self.restored_at - self.backup_created_at


def complete_backups(storage: Storage) -> list[str]:
    """Stamps of backups whose manifest was uploaded, oldest first."""
    return sorted(
        name.split("/", 1)[0]
        for name in storage.list()
        if name.count("/") == 1 and name.endswith("/" + MANIFEST)
    )


def _fetch(storage: Storage, name: str, work: Path) -> Path:
    target = work / name.replace("/", "_")
    storage.get(name, target)
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(storage: Storage, stamp: str, secret: X25519PrivateKey) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="velora-manifest-") as tmp:
        path = _fetch(storage, f"{stamp}/{MANIFEST}", Path(tmp))
        try:
            manifest: dict[str, Any] = json.loads(decrypt_bytes(path.read_bytes(), secret))
        except BackupCryptoError as exc:
            raise RestoreError(f"manifest of {stamp}: {exc}") from exc
    if manifest.get("format") != FORMAT or manifest.get("stamp") != stamp:
        raise RestoreError(f"manifest of {stamp} is not a format-{FORMAT} manifest for it")
    return manifest


def role_attributes(roles_sql: str) -> dict[str, dict[str, bool]]:
    """Attributes of the VELORA roles in a `pg_dumpall --roles-only` script."""
    found: dict[str, dict[str, bool]] = {}
    for line in roles_sql.splitlines():
        match = re.match(r"^ALTER ROLE (\w+) WITH (.*);$", line.strip())
        if match is None or match[1] not in VELORA_ROLES:
            continue
        flags: dict[str, bool] = {}
        for token in match[2].split():
            for name, column in _ROLE_FLAGS.items():
                if token == name:
                    flags[column] = True
                elif token == "NO" + name:
                    flags[column] = False
        found[match[1]] = flags
    return found


def _check_roles(conn: psycopg.Connection[Any], expected: dict[str, dict[str, bool]]) -> Check:
    problems = []
    for role in VELORA_ROLES:
        row = conn.execute(
            "SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, "
            "rolreplication, rolbypassrls FROM pg_roles WHERE rolname = %s",
            (role,),
        ).fetchone()
        if row is None:
            problems.append(f"{role} missing")
            continue
        actual = dict(zip(_ROLE_FLAGS.values(), row, strict=True))
        if actual["rolsuper"]:
            problems.append(f"{role} is a superuser")
        if actual["rolbypassrls"] and role not in BYPASS_ALLOWED:
            problems.append(f"{role} can bypass RLS")
        for column, value in expected.get(role, {}).items():
            if actual[column] != value:
                problems.append(f"{role}.{column} is {actual[column]}, backup had {value}")
    return Check("roles", not problems, "; ".join(problems))


def _apply_database_acl(conn: psycopg.Connection[Any], dbname: str, acl: list[str]) -> None:
    db = sql.Identifier(dbname)
    conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db))
    for item in acl:
        grantee, _, rest = item.partition("=")
        privileges = [_PRIVILEGES[c] for c in rest.split("/", 1)[0] if c in _PRIVILEGES]
        if not privileges:
            continue
        target = sql.SQL("PUBLIC") if grantee == "" else sql.Identifier(grantee.strip('"'))
        conn.execute(
            sql.SQL("GRANT {} ON DATABASE {} TO {}").format(
                sql.SQL(", ").join(sql.SQL(p) for p in privileges), db, target
            )
        )


def verify_restored(admin_url: str, dbname: str, manifest: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    with psycopg.connect(make_conninfo(admin_url, dbname=dbname), autocommit=True) as conn:
        facts = describe(conn)
        latest = load_migrations()[-1].version
        version = facts["schema_version"]
        checks.append(
            Check(
                "schema version",
                version == manifest["schema_version"],
                f"restored {version}, backup {manifest['schema_version']}, code {latest}"
                + ("" if version == latest else " (run `migrate up` before use)"),
            )
        )
        wrong = {
            t: (facts["row_counts"].get(t), n)
            for t, n in manifest["row_counts"].items()
            if facts["row_counts"].get(t) != n
        }
        checks.append(
            Check(
                "row counts",
                not wrong and facts["row_counts"].keys() == manifest["row_counts"].keys(),
                f"{len(manifest['row_counts'])} tables, "
                f"{sum(manifest['row_counts'].values())} rows"
                + (f"; differ (restored, backup): {wrong}" if wrong else ""),
            )
        )
        checks.append(
            Check(
                "FORCE RLS and policies",
                facts["force_rls"] == manifest["force_rls"],
                f"{len(manifest['force_rls'])} tables"
                if facts["force_rls"] == manifest["force_rls"]
                else "differs from the backup: "
                f"missing {sorted(set(manifest['force_rls']) - set(facts['force_rls']))}, "
                f"changed {[t for t in facts['force_rls'] if t in manifest['force_rls'] and facts['force_rls'][t] != manifest['force_rls'][t]]}",  # noqa: E501
            )
        )
        checks.append(
            Check(
                "database grants",
                facts["database_acl"] == manifest["database_acl"],
                ", ".join(facts["database_acl"]),
            )
        )
        checks.append(_check_isolation(conn, sorted(manifest["force_rls"])))
    return checks


def _check_isolation(conn: psycopg.Connection[Any], tables: list[str]) -> Check:
    tenants = [r[0] for r in conn.execute("SELECT id FROM velora.tenants ORDER BY id").fetchall()]
    problems: list[str] = []
    with conn.transaction():
        own: dict[tuple[str, Any], int] = {}
        keys: dict[str, str] = {}
        for table in tables:
            schema, name = table.split(".", 1)
            key = "id" if (schema, name) == ("velora", "tenants") else "tenant_id"
            has_key = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
                (schema, name, key),
            ).fetchone()
            if has_key is None:
                problems.append(f"{table} has FORCE RLS but no {key}")
                continue
            keys[table] = key
            for tenant, count in conn.execute(
                sql.SQL("SELECT {k}, count(*) FROM {s}.{t} GROUP BY 1").format(
                    k=sql.Identifier(key), s=sql.Identifier(schema), t=sql.Identifier(name)
                )
            ).fetchall():
                own[(table, tenant)] = int(count)
        conn.execute("SET LOCAL ROLE app_rw")
        for table, key in keys.items():
            schema, name = table.split(".", 1)
            relation = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(name))
            conn.execute("SELECT set_config('app.tenant_id', '', true)")
            (unscoped,) = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(relation)
            ).fetchone() or (0,)
            if unscoped:
                problems.append(f"{table}: {unscoped} rows visible with no tenant set")
            for tenant in tenants:
                conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
                seen, foreign = conn.execute(
                    sql.SQL("SELECT count(*), count(*) FILTER (WHERE {k} <> %s) FROM {r}").format(
                        k=sql.Identifier(key), r=relation
                    ),
                    (tenant,),
                ).fetchone() or (0, 0)
                if foreign:
                    problems.append(f"{table}: tenant {tenant} sees {foreign} foreign rows")
                if seen != own.get((table, tenant), 0):
                    problems.append(f"{table}: tenant {tenant} sees {seen} of its rows")
        conn.execute("RESET ROLE")
    return Check(
        "tenant isolation (as app_rw)",
        not problems,
        f"{len(keys)} tables x {len(tenants)} tenants"
        + (f"; {'; '.join(problems[:5])}" if problems else ""),
    )


def _pg_restore(
    tools: PgTools, admin_url: str, dbname: str, dump: Path, identity: X25519PrivateKey
) -> None:
    argv, env = tools.command(
        "pg_restore", admin_url, "--exit-on-error", "--no-comments", "--dbname", dbname
    )
    with (
        tempfile.TemporaryFile() as errors_file,
        dump.open("rb") as source,
        subprocess.Popen(  # noqa: S603 - argv from settings, no shell
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=errors_file,
            env=env,
        ) as proc,
    ):
        stdin = proc.stdin
        if stdin is None:  # pragma: no cover - stdin=PIPE always gives one
            raise RestoreError("pg_restore has no input stream")
        try:
            for chunk in decrypt_stream(source, identity):
                stdin.write(chunk)
        except (BackupCryptoError, BrokenPipeError) as exc:
            proc.kill()
            raise RestoreError(f"the database dump could not be restored: {exc}") from exc
        finally:
            if not stdin.closed:
                stdin.close()
        code = proc.wait()
        errors_file.seek(0)
        errors = errors_file.read().decode("utf-8", errors="replace").strip()
    if code != 0:
        raise RestoreError(
            f"pg_restore failed: {errors.splitlines()[-1][:200] if errors else code}"
        )


def restore_backup(
    *,
    storage: Storage,
    stamp: str | None,
    secret: X25519PrivateKey,
    admin_url: str,
    dbname: str,
    tools: PgTools,
    now: datetime,
    work_dir: Path | None = None,
) -> RestoreReport:
    """Restore `stamp` (default: the newest complete backup) into the new database `dbname`.
    The database is dropped again if the restore itself fails; it is kept, for inspection,
    if only a check fails (the report says so)."""
    if not _DBNAME.match(dbname):
        raise RestoreError("the target database name must be lower-case letters, digits and _")
    started = time.monotonic()
    if stamp is None:
        stamps = complete_backups(storage)
        if not stamps:
            raise RestoreError("no complete backup found")
        stamp = stamps[-1]
    manifest = read_manifest(storage, stamp, secret)
    work = Path(tempfile.mkdtemp(prefix="velora-restore-", dir=work_dir))
    created = False
    try:
        paths = {}
        for name in (ROLES, DATABASE):
            path = _fetch(storage, f"{stamp}/{name}", work)
            expected = manifest["files"][name]
            if _sha256(path) != expected["sha256"] or path.stat().st_size != expected["bytes"]:
                raise RestoreError(f"{stamp}/{name} doesn't match its manifest")
            paths[name] = path
        try:
            roles_sql = decrypt_bytes(paths[ROLES].read_bytes(), secret).decode("utf-8")
        except BackupCryptoError as exc:
            raise RestoreError(f"roles: {exc}") from exc

        setup_roles(admin_url)
        with psycopg.connect(admin_url, autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
            ).fetchone()
            if exists is not None:
                raise RestoreError(f"database {dbname} already exists; restore into a new one")
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER velora_owner").format(sql.Identifier(dbname))
            )
            created = True
            role_check = _check_roles(conn, role_attributes(roles_sql))
        _pg_restore(tools, admin_url, dbname, paths[DATABASE], secret)
        with psycopg.connect(admin_url, autocommit=True) as conn:
            _apply_database_acl(conn, dbname, manifest["database_acl"])
        checks = [role_check, *verify_restored(admin_url, dbname, manifest)]
    except Exception as exc:
        if created:
            with psycopg.connect(admin_url, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(dbname)
                    )
                )
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError(f"restore failed: {error_text(exc)}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return RestoreReport(
        stamp=stamp,
        database=dbname,
        backup_created_at=datetime.fromisoformat(manifest["created_at"]),
        restored_at=now + timedelta(seconds=time.monotonic() - started),
        duration=timedelta(seconds=time.monotonic() - started),
        checks=checks,
    )
