"""Database command line: `python -m velora.db <command>`.

setup-roles          create or re-assert cluster roles  (VELORA_ADMIN_DATABASE_URL)
migrate up [N]       apply migrations up to N            (VELORA_MIGRATION_DATABASE_URL)
migrate down [N]     revert migrations above N (dev)     (VELORA_MIGRATION_DATABASE_URL)
migrate status       list applied versions               (VELORA_MIGRATION_DATABASE_URL)
"""

from __future__ import annotations

import argparse
import os
import sys

from velora.config import Settings
from velora.db.migrate import (
    applied_versions,
    load_migrations,
    migrate_down,
    migrate_up,
    setup_roles,
)


def _url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value == "CHANGE_ME":
        raise SystemExit(f"{name} is not set")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m velora.db")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-roles")
    migrate = commands.add_parser("migrate")
    migrate.add_argument("action", choices=["up", "down", "status"])
    migrate.add_argument("target", nargs="?", type=int)
    args = parser.parse_args(argv)

    if args.command == "setup-roles":
        setup_roles(_url("VELORA_ADMIN_DATABASE_URL"))
        print("roles ready")
        return 0

    url = _url("VELORA_MIGRATION_DATABASE_URL")
    if args.action == "status":
        applied = set(applied_versions(url))
        for m in load_migrations():
            print(f"{'applied' if m.version in applied else 'pending':8} {m.version:04d}_{m.name}")
    elif args.action == "up":
        print(f"applied: {migrate_up(url, args.target) or 'nothing'}")
    else:
        env = Settings.from_env().env
        if env not in {"development", "test"}:
            print(f"refusing to migrate down in {env!r}: fix forward instead", file=sys.stderr)
            return 2
        print(f"reverted: {migrate_down(url, args.target or 0) or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
