"""The backup job (P11-T1, P11-T2, AD-21, AD-36).

`run_backup` makes one backup and uploads it as three encrypted objects under a timestamp:

    <stamp>/roles.sql.vbk       cluster roles: `pg_dumpall --roles-only --no-role-passwords`
    <stamp>/database.dump.vbk   the database: `pg_dump --format=custom`, with its grants,
                                RLS settings and policies
    <stamp>/manifest.json.vbk   what a restore checks against: row counts per table, schema
                                version, tables under FORCE RLS and their policies, the
                                database's own grants (not in a plain pg_dump), and each
                                file's SHA-256. Uploaded last: a backup without it is
                                incomplete and is never restored.

**Consistency.** The row counts and the dump come from one exported snapshot
(`pg_export_snapshot`, `pg_dump --snapshot`), so a restore can be checked row for row.

**No plaintext on disk.** pg_dump's output is read from its pipe and encrypted chunk by chunk
in memory; only ciphertext is written, to a work directory that is emptied afterwards.

**Only the database.** OpenWA's session volume is never part of a backup (P11-T2, PC-8):
it holds WhatsApp login state in plaintext. After a host loss, sessions are re-linked by QR.

Role passwords aren't in the dump. After a restore, set them again (runbook).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any

import psycopg
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from velora.backup.config import PgTools
from velora.backup.crypto import Encryptor, encrypt_bytes
from velora.backup.storage import Storage
from velora.redaction import error_text

FORMAT = 1
ROLES = "roles.sql.vbk"
DATABASE = "database.dump.vbk"
MANIFEST = "manifest.json.vbk"
SCHEMAS = ("velora", "velora_migrations")
PIPE_BLOCK = 1 << 16


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupResult:
    stamp: str
    created_at: datetime
    objects: list[str]
    bytes_uploaded: int
    tables: int
    rows: int


def stamp_of(at: datetime) -> str:
    return at.strftime("%Y%m%dT%H%M%SZ")


def _tables(conn: psycopg.Connection[Any]) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT n.nspname, c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p') ORDER BY 1, 2",
        (list(SCHEMAS),),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def describe(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """What a restored copy must match. Run inside the dump's snapshot."""
    counts = {
        f"{schema}.{table}": int(
            conn.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            ).fetchone()[0]  # type: ignore[index]
        )
        for schema, table in _tables(conn)
    }
    rls = conn.execute(
        "SELECT n.nspname || '.' || c.relname, "
        "coalesce(array_agg(p.polname ORDER BY p.polname) FILTER (WHERE p.polname IS NOT NULL), "
        "'{}') FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_policy p ON p.polrelid = c.oid "
        "WHERE n.nspname = 'velora' AND c.relkind = 'r' AND c.relforcerowsecurity "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    (acl,) = conn.execute(
        "SELECT coalesce(datacl::text[], '{}') FROM pg_database WHERE datname = current_database()"
    ).fetchone() or ([],)
    (version,) = conn.execute("SELECT max(version) FROM velora_migrations.applied").fetchone() or (
        None,
    )
    (server,) = conn.execute("SHOW server_version").fetchone() or ("",)
    return {
        "row_counts": counts,
        "force_rls": {name: list(policies) for name, policies in rls},
        "database_acl": sorted(acl),
        "schema_version": version,
        "server_version": server,
    }


def _run_to(
    tool: str, cmd: list[str], env: dict[str, str], sink: IO[bytes], recipient: X25519PublicKey
) -> str:
    """Run a dump tool, encrypting its stdout into `sink`. Returns the SHA-256 written."""
    digest = hashlib.sha256()
    enc = Encryptor(recipient)
    # stderr goes to a file (it names objects, never row data), so a chatty tool can't block
    # on a full pipe while we read stdout.
    with (
        tempfile.TemporaryFile() as errors_file,
        subprocess.Popen(  # noqa: S603 - argv from settings, no shell
            cmd, stdout=subprocess.PIPE, stderr=errors_file, env=env
        ) as proc,
    ):
        stdout = proc.stdout
        if stdout is None:  # pragma: no cover - stdout=PIPE always gives one
            raise BackupError(f"{tool} produced no output stream")
        for block in iter(lambda: stdout.read(PIPE_BLOCK), b""):
            out = enc.update(block)
            sink.write(out)
            digest.update(out)
        code = proc.wait()
        errors_file.seek(0)
        errors = errors_file.read()
    if code != 0:
        # The tools' messages name objects, never row data.
        raise BackupError(f"{tool} failed: {_tail(errors)}")
    out = enc.finish()
    sink.write(out)
    digest.update(out)
    return digest.hexdigest()


def _tail(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace").strip().splitlines()
    return text[-1][:200] if text else "no error output"


def _dbname(url: str) -> str:
    name = conninfo_to_dict(url).get("dbname")
    if not name:
        raise BackupError("the backup database URL names no database")
    return str(name)


def run_backup(
    *,
    database_url: str,
    recipient: X25519PublicKey,
    storage: Storage,
    tools: PgTools,
    now: datetime,
    work_dir: Path | None = None,
) -> BackupResult:
    """Make one backup, as the backup role, and upload it. Raises BackupError on failure;
    nothing incomplete is ever marked complete (no manifest)."""
    stamp = stamp_of(now)
    dbname = _dbname(database_url)
    work = Path(tempfile.mkdtemp(prefix="velora-backup-", dir=work_dir))
    try:
        files: dict[str, dict[str, Any]] = {}
        with psycopg.connect(database_url) as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            with conn.transaction():
                (snapshot,) = conn.execute("SELECT pg_export_snapshot()").fetchone() or ("",)
                facts = describe(conn)
                # pg_dump must start while this transaction holds the snapshot open.
                argv, env = tools.command(
                    "pg_dump",
                    database_url,
                    "--format=custom",
                    f"--snapshot={snapshot}",
                    "--dbname",
                    dbname,
                )
                target = work / DATABASE
                with target.open("wb") as sink:
                    digest = _run_to("pg_dump", argv, env, sink, recipient)
                files[DATABASE] = {"sha256": digest, "bytes": target.stat().st_size}

        argv, env = tools.command(
            "pg_dumpall", database_url, "--roles-only", "--no-role-passwords", "--database", dbname
        )
        target = work / ROLES
        with target.open("wb") as sink:
            digest = _run_to("pg_dumpall", argv, env, sink, recipient)
        files[ROLES] = {"sha256": digest, "bytes": target.stat().st_size}

        manifest = {
            "format": FORMAT,
            "stamp": stamp,
            "created_at": now.isoformat(),
            "database": dbname,
            "files": files,
            **facts,
        }
        (work / MANIFEST).write_bytes(
            encrypt_bytes(json.dumps(manifest, sort_keys=True).encode("utf-8"), recipient)
        )
        uploaded = 0
        objects = []
        for name in (ROLES, DATABASE, MANIFEST):  # the manifest last: it marks completion
            storage.put(f"{stamp}/{name}", work / name)
            uploaded += (work / name).stat().st_size
            objects.append(f"{stamp}/{name}")
        counts: dict[str, int] = facts["row_counts"]
        return BackupResult(stamp, now, objects, uploaded, len(counts), sum(counts.values()))
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(f"backup failed: {error_text(exc)}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
