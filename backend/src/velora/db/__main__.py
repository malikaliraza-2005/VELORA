"""Database command line: `python -m velora.db <command>`.

setup-roles          create or re-assert cluster roles  (VELORA_ADMIN_DATABASE_URL)
provision            roles, role passwords and the database for a deployment (P12):
                     VELORA_ADMIN_DATABASE_URL, plus each role's connection setting, whose
                     password is set on the role (velora.db.migrate.ROLE_URL_SETTINGS)
migrate up [N]       apply migrations up to N            (VELORA_MIGRATION_DATABASE_URL)
migrate down [N]     revert migrations above N (dev)     (VELORA_MIGRATION_DATABASE_URL)
migrate status       list applied versions               (VELORA_MIGRATION_DATABASE_URL)
"""

from __future__ import annotations

import argparse
import os
import sys

from psycopg.conninfo import conninfo_to_dict

from velora.config import Settings
from velora.db.migrate import (
    ROLE_URL_SETTINGS,
    applied_versions,
    load_migrations,
    migrate_down,
    migrate_up,
    provision,
    setup_roles,
)


def _url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value == "CHANGE_ME":
        raise SystemExit(f"{name} is not set")
    return value


def _role_passwords() -> tuple[str, dict[str, str]]:
    """The database and each role's password, from the roles' connection settings. Every
    setting must name its own role and the same database."""
    database: str | None = None
    passwords: dict[str, str] = {}
    for role, name in ROLE_URL_SETTINGS.items():
        value = os.environ.get(name, "").strip()
        if not value or value == "CHANGE_ME":
            continue
        params = conninfo_to_dict(value)
        if params.get("user") != role:
            raise SystemExit(f"{name} must connect as {role}")
        if not params.get("password"):
            raise SystemExit(f"{name} has no password")
        dbname = str(params.get("dbname", ""))
        if database is None:
            database = dbname
        elif dbname != database:
            raise SystemExit(f"{name} names database {dbname!r}, not {database!r}")
        passwords[role] = str(params["password"])
    if not database or "velora_owner" not in passwords:
        raise SystemExit("VELORA_MIGRATION_DATABASE_URL is not set")
    return database, passwords


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m velora.db")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-roles")
    commands.add_parser("provision")
    migrate = commands.add_parser("migrate")
    migrate.add_argument("action", choices=["up", "down", "status"])
    migrate.add_argument("target", nargs="?", type=int)
    args = parser.parse_args(argv)

    if args.command == "setup-roles":
        setup_roles(_url("VELORA_ADMIN_DATABASE_URL"))
        print("roles ready")
        return 0

    if args.command == "provision":
        database, passwords = _role_passwords()
        created = provision(_url("VELORA_ADMIN_DATABASE_URL"), database, passwords)
        print(f"roles ready; passwords set for {', '.join(sorted(passwords))}")
        print(f"database {database} {'created' if created else 'already exists'}")
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
