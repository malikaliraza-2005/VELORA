"""Backups and restore: `python -m velora.backup <command>` (P11).

keygen --public-out FILE --secret-out FILE
        make the key pair. The PUBLIC key goes in VELORA_BACKUP_PUBLIC_KEY on the server; the
        SECRET key is kept offline (password manager, plus a paper copy) and never on the
        server. Without it no backup can be read.
run     make one backup and upload it; reports the run to the monitor (hourly from a timer,
        P12). Needs VELORA_BACKUP_DATABASE_URL, VELORA_BACKUP_PUBLIC_KEY, VELORA_BACKUP_STORAGE.
list    complete backups in storage, oldest first.
restore --secret-key FILE --target-db NAME [--stamp STAMP] [--json]
        restore the newest (or the given) backup into a NEW database and verify it.
        Needs VELORA_ADMIN_DATABASE_URL and the storage settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from psycopg import AsyncConnection

from velora.backup.config import BackupSettings
from velora.backup.crypto import (
    BackupCryptoError,
    generate_keypair,
    public_key_from_text,
    secret_key_from_text,
)
from velora.backup.job import BackupError, run_backup
from velora.backup.restore import RestoreError, RestoreReport, complete_backups, restore_backup
from velora.backup.storage import StorageError
from velora.clock import SystemClock
from velora.config import Settings
from velora.monitoring.metrics import record_backup
from velora.redaction import configure_logging, error_text

BACKUP_KIND = "hourly-dump"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m velora.backup")
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen")
    keygen.add_argument("--public-out", type=Path, required=True)
    keygen.add_argument("--secret-out", type=Path, required=True)
    commands.add_parser("run")
    commands.add_parser("list")
    restore = commands.add_parser("restore")
    restore.add_argument("--secret-key", type=Path, required=True)
    restore.add_argument("--target-db", required=True)
    restore.add_argument("--stamp")
    restore.add_argument("--json", action="store_true")
    return parser


def _write_new(path: Path, text: str, *, private: bool) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text + "\n")


async def _report(url: str | None, *, ok: bool, detail: str | None) -> None:
    if url is None:
        print("warning: VELORA_MONITOR_DATABASE_URL not set; run not reported", file=sys.stderr)
        return
    async with await AsyncConnection.connect(url) as conn:
        await record_backup(conn, ok=ok, kind=BACKUP_KIND, detail=detail, at=SystemClock().now())


def report_run(url: str | None, *, ok: bool, detail: str | None) -> None:
    # psycopg's async mode cannot use the Windows proactor event loop.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(_report(url, ok=ok, detail=detail), loop_factory=loop)


def restore_text(report: RestoreReport) -> str:
    lines = [
        f"Restored {report.stamp} into {report.database}: {'OK' if report.ok else 'FAILED'}",
        f"  backup taken  {report.backup_created_at.isoformat()}",
        f"  data age      {report.data_age} (RPO target 1 h, from the newest backup)",
        f"  restore took  {report.duration} (RTO target 4 h, for the whole procedure)",
    ]
    lines += [f"  [{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in report.checks]
    return "\n".join(lines)


def _run(settings: BackupSettings) -> int:
    clock = SystemClock()
    if settings.database_url is None or settings.public_key is None:
        print(
            "VELORA_BACKUP_DATABASE_URL and VELORA_BACKUP_PUBLIC_KEY must be set", file=sys.stderr
        )
        return 2
    try:
        result = run_backup(
            database_url=settings.database_url,
            recipient=public_key_from_text(settings.public_key),
            storage=settings.storage(clock),
            tools=settings.tools,
            now=clock.now(),
            work_dir=settings.work_dir,
        )
    except (BackupError, BackupCryptoError, StorageError) as exc:
        detail = error_text(exc)
        print(f"backup FAILED: {detail}", file=sys.stderr)
        report_run(settings.monitor_database_url, ok=False, detail=detail)
        return 1
    report_run(
        settings.monitor_database_url,
        ok=True,
        detail=f"{result.stamp}: {result.tables} tables, {result.rows} rows",
    )
    print(
        f"backup {result.stamp}: {result.tables} tables, {result.rows} rows, "
        f"{result.bytes_uploaded} bytes uploaded"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base = Settings.from_env()
    configure_logging(base.log_level, fmt=base.log_format)
    if args.command == "keygen":
        public, secret = generate_keypair()
        try:
            _write_new(args.secret_out, secret, private=True)
            _write_new(args.public_out, public, private=False)
        except FileExistsError as exc:
            print(f"refusing to overwrite {exc.filename}", file=sys.stderr)
            return 2
        print(f"public key: {public}")
        print(f"secret key written to {args.secret_out}: keep it OFF the server")
        return 0
    try:
        settings = BackupSettings.from_env()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.command == "run":
        return _run(settings)
    clock = SystemClock()
    try:
        storage = settings.storage(clock)
        if args.command == "list":
            for stamp in complete_backups(storage):
                print(stamp)
            return 0
        admin_url = os.environ.get("VELORA_ADMIN_DATABASE_URL", "").strip()
        if not admin_url or admin_url == "CHANGE_ME":
            print("VELORA_ADMIN_DATABASE_URL must be set", file=sys.stderr)
            return 2
        report = restore_backup(
            storage=storage,
            stamp=args.stamp,
            secret=secret_key_from_text(args.secret_key.read_text(encoding="utf-8")),
            admin_url=admin_url,
            dbname=args.target_db,
            tools=settings.tools,
            now=clock.now(),
            work_dir=settings.work_dir,
        )
    except (RestoreError, BackupCryptoError, StorageError, OSError) as exc:
        print(f"restore FAILED: {error_text(exc)}", file=sys.stderr)
        return 1
    if args.json:
        print(
            json.dumps(
                {
                    "stamp": report.stamp,
                    "database": report.database,
                    "ok": report.ok,
                    "backup_created_at": report.backup_created_at.isoformat(),
                    "data_age_seconds": report.data_age.total_seconds(),
                    "duration_seconds": report.duration.total_seconds(),
                    "checks": [c.__dict__ for c in report.checks],
                },
                indent=2,
            )
        )
    else:
        print(restore_text(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
