"""`python -m velora.dev <command>`: run VELORA on your own computer, with simulated WhatsApp.

setup --admin-url URL   prepare a local database and a demo restaurant, write `.env.dev`,
                        and print the owner's login link. Use a PostgreSQL server of its
                        own: this sets cluster-wide role passwords. `--reset` starts over.
link                    print a new owner login link (links last 15 minutes)
api                     run the API and the staff dashboard on VELORA_PUBLIC_BASE_URL
customer [--phone P]    chat as a WhatsApp customer. This also runs the worker (inbox,
                        outbox, timers, health poll), so keep it open while you test.
monitor ARGS...         the founder's monitor (P10) on the demo database, e.g.
                        `monitor status` or `monitor daily --day 2026-10-21`

Settings come from `.env.dev` in the repository root (written by `setup`); your own `.env`
is never read or changed. Development only. This is a composition root: it may wire in the
`FakeProvider`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import secrets
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import AsyncConnectionPool

from velora.admin.onboarding import Onboarding
from velora.clock import SystemClock
from velora.config import Settings
from velora.conversation.engine import ConversationEngine
from velora.db import Database, TenantId
from velora.db.migrate import migrate_up, setup_roles
from velora.dev.customer import Customer
from velora.dev.demo import SESSION_REF, create_demo
from velora.messaging.channels import ChannelMonitor
from velora.messaging.dispatch import Dispatcher
from velora.messaging.inbox import InboxProcessor
from velora.messaging.outbox import OutboxSender, apply_delivery_status
from velora.messaging.providers.fake import FakeProvider
from velora.orders.timers import OrderTimers
from velora.workers.runner import Worker

ENV_FILE = Path(__file__).resolve().parents[4] / ".env.dev"
DATABASE = "velora_dev"
PORT = 8000
ROLES = ("velora_owner", "app_rw", "velora_platform", "velora_dispatcher", "velora_monitor")
# psycopg's async mode cannot use the Windows proactor event loop.
LOOP = asyncio.SelectorEventLoop if sys.platform == "win32" else None


def _load_env() -> None:
    if not ENV_FILE.is_file():
        sys.exit(f"{ENV_FILE} not found: run `python -m velora.dev setup` first")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()
    _require_development()


def _require_development() -> None:
    if os.environ.get("VELORA_ENV", "development") != "development":
        sys.exit("velora.dev runs only with VELORA_ENV=development")


def _settings() -> Settings:
    _load_env()
    return Settings.from_env()


# setup / link ---------------------------------------------------------------------------


def setup(admin_url: str, *, reset: bool) -> None:
    _require_development()
    host = str(conninfo_to_dict(admin_url).get("host", ""))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        sys.exit("setup only works on a PostgreSQL server on this computer")
    setup_roles(admin_url)
    passwords = {role: secrets.token_urlsafe(24) for role in ROLES}
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,))
        if exists.fetchone() is not None:
            if not reset:
                sys.exit(f"database {DATABASE} already exists; use --reset to start over")
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(DATABASE)))
        for role, password in passwords.items():
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER velora_owner").format(sql.Identifier(DATABASE))
        )
    urls = {
        role: make_conninfo(admin_url, dbname=DATABASE, user=role, password=password)
        for role, password in passwords.items()
    }
    migrate_up(urls["velora_owner"])
    base_url = f"http://127.0.0.1:{PORT}"
    settings = Settings(env="development", log_level="INFO", public_base_url=base_url)
    with psycopg.connect(urls["velora_platform"]) as conn:
        demo = asyncio.run(
            create_demo(
                Onboarding(conn, operator="velora.dev setup"),
                urls["velora_platform"],
                operator="velora.dev setup",
                magic_link_url=settings.magic_link_url,
            ),
            loop_factory=LOOP,
        )
    ENV_FILE.write_text(
        "# Written by `python -m velora.dev setup`. Local development only; never commit.\n"
        "VELORA_ENV=development\n"
        "VELORA_LOG_LEVEL=INFO\n"
        "VELORA_LOG_FORMAT=text\n"
        f"VELORA_PUBLIC_BASE_URL={base_url}\n"
        f"VELORA_DATABASE_URL={urls['app_rw']}\n"
        f"VELORA_DISPATCHER_DATABASE_URL={urls['velora_dispatcher']}\n"
        f"VELORA_MONITOR_DATABASE_URL={urls['velora_monitor']}\n"
        f"VELORA_PLATFORM_DATABASE_URL={urls['velora_platform']}\n"
        f"VELORA_MIGRATION_DATABASE_URL={urls['velora_owner']}\n"
        "VELORA_OPERATOR=velora.dev\n"
        f"VELORA_DEV_TENANT={demo.tenant.value}\n"
        f"VELORA_DEV_OWNER={demo.owner_id}\n",
        encoding="utf-8",
    )
    print(f"Demo restaurant ready. Settings written to {ENV_FILE}.")
    print("Owner login link (works once, for 15 minutes):")
    print(demo.owner_link)


def link() -> None:
    settings = _settings()
    with psycopg.connect(os.environ["VELORA_PLATFORM_DATABASE_URL"]) as conn:
        invite = Onboarding(conn, operator="velora.dev link").recovery_link(
            TenantId(uuid.UUID(os.environ["VELORA_DEV_TENANT"])),
            uuid.UUID(os.environ["VELORA_DEV_OWNER"]),
        )
    print("Owner login link (works once, for 15 minutes):")
    print(settings.magic_link_url(invite.token.wire()))


# api --------------------------------------------------------------------------------------


def api() -> None:
    import uvicorn  # noqa: PLC0415 - a development dependency

    _load_env()
    from velora.asgi import app  # noqa: PLC0415 - reads the environment when imported

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info"))
    print(f"Dashboard: http://127.0.0.1:{PORT}/  (Ctrl+C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(server.serve(), loop_factory=LOOP)


# customer ---------------------------------------------------------------------------------

HELP = """Type a message and press Enter to send it as the customer.
  /as +92300...   switch to another customer number
  /quit           stop (this also stops the worker)
Try: hi, MENU, 1, CART, CHECKOUT, YES, status, 0 (talk to staff), STOP."""


async def _customer(phone: str) -> None:
    # Replies contain emoji; a Windows console or pipe may not default to UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    settings = _settings()
    clock = SystemClock()
    provider = FakeProvider()
    providers = {"fake": provider}
    if settings.database_url is None or settings.dispatcher_database_url is None:
        sys.exit(f"database URLs missing from {ENV_FILE}; run setup again")
    async with (
        AsyncConnectionPool(settings.database_url, open=False) as app_pool,
        AsyncConnectionPool(
            settings.dispatcher_database_url, open=False, min_size=1, max_size=2
        ) as d_pool,
    ):
        db = Database(app_pool)
        tenant = await db.resolve_channel_tenant("fake", SESSION_REF)
        if tenant is None:
            sys.exit("the demo restaurant's channel was not found; run setup again")
        monitor = ChannelMonitor(db, providers, clock)
        # The simulated phone is linked: record "connected" now rather than at the next poll.
        await monitor.poll(tenant)
        engine = ConversationEngine(clock, providers, None, ai_timeout=settings.ai_timeout_seconds)
        worker = Worker(
            Dispatcher(d_pool, clock),
            InboxProcessor(
                db, clock, {"message": engine.handle, "delivery_status": apply_delivery_status}
            ),
            OutboxSender(db, providers, clock),
            monitor,
            clock,
            timers=OrderTimers(db, clock),
        )
        stop = asyncio.Event()
        work = asyncio.create_task(worker.run_forever(stop))
        customer = Customer(db, provider, clock, SESSION_REF, phone)

        async def show_replies() -> None:
            while not stop.is_set():
                for sent in customer.new_replies():
                    who = "" if sent.to == customer.phone else f" (to {sent.to})"
                    print(
                        f"\n  VELORA{who}: "
                        + (sent.text or "[media]").replace("\n", "\n          ")
                    )
                await asyncio.sleep(0.3)

        printer = asyncio.create_task(show_replies())
        print(HELP)
        print(f"You are {phone}.")
        try:
            while True:
                line = (await asyncio.to_thread(input, "")).strip()
                if not line:
                    continue
                if line == "/quit":
                    break
                if line.startswith("/as "):
                    customer.phone = line[4:].strip()
                    print(f"You are now {customer.phone}.")
                    continue
                await customer.say(line)
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            stop.set()
            await work
            printer.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m velora.dev", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    set_up = commands.add_parser("setup")
    set_up.add_argument("--admin-url", required=True, help="superuser URL of a local PostgreSQL")
    set_up.add_argument("--reset", action="store_true", help="delete the demo database first")
    commands.add_parser("link")
    commands.add_parser("api")
    chat = commands.add_parser("customer")
    chat.add_argument("--phone", default="+923001234567")
    watch = commands.add_parser("monitor")
    watch.add_argument("rest", nargs=argparse.REMAINDER, help="velora.monitoring arguments")
    args = parser.parse_args()
    if args.command == "setup":
        setup(args.admin_url, reset=args.reset)
    elif args.command == "link":
        link()
    elif args.command == "api":
        api()
    elif args.command == "monitor":
        _load_env()
        from velora.monitoring.__main__ import main as monitoring  # noqa: PLC0415

        return monitoring(args.rest)
    else:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_customer(args.phone), loop_factory=LOOP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
