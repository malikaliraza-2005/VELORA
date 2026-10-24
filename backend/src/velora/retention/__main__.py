"""Data retention by hand: `python -m velora.retention <command>` (AD-38).

The worker already sweeps every tenant hourly; these are for the founder.

run                                   one sweep of every tenant now
                                      (VELORA_DATABASE_URL, VELORA_DISPATCHER_DATABASE_URL)
forget --tenant SLUG --phone +92...   remove one customer's personal data now, on their
                                      request (VELORA_DATABASE_URL, and
                                      VELORA_PLATFORM_DATABASE_URL to find the restaurant).
                                      Needs an operator name (--operator or VELORA_OPERATOR)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import psycopg
from psycopg_pool import AsyncConnectionPool

from velora.admin.onboarding import Onboarding, OnboardingError
from velora.clock import SystemClock
from velora.config import PLACEHOLDER, Settings
from velora.db import Database, TenantId
from velora.messaging.dispatch import Dispatcher
from velora.redaction import configure_logging
from velora.retention import ForgetRefusedError, Retention, RetentionReport

# psycopg's async mode cannot use the Windows proactor event loop.
LOOP = asyncio.SelectorEventLoop if sys.platform == "win32" else None


def _url(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return None if not value or value == PLACEHOLDER else value


async def _run(app_url: str, dispatcher_url: str) -> RetentionReport:
    clock = SystemClock()
    total = RetentionReport()
    async with (
        AsyncConnectionPool(app_url, open=False) as app_pool,
        AsyncConnectionPool(dispatcher_url, open=False, min_size=1, max_size=1) as d_pool,
    ):
        retention = Retention(Database(app_pool), clock)
        for tenant in await Dispatcher(d_pool, clock).tenants_for_recovery():
            # Repeat each tenant until nothing is left, batch by batch.
            while (report := await retention.sweep(tenant)).total:
                for key, value in vars(report).items():
                    setattr(total, key, getattr(total, key) + value)
    return total


async def _forget(app_url: str, tenant: TenantId, phone: str, operator: str) -> RetentionReport:
    async with AsyncConnectionPool(app_url, open=False, min_size=1, max_size=1) as pool:
        return await Retention(Database(pool), SystemClock()).forget(
            tenant, phone, operator=operator
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m velora.retention")
    parser.add_argument("--operator", default=os.environ.get("VELORA_OPERATOR", ""))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    forget = commands.add_parser("forget")
    forget.add_argument("--tenant", required=True, help="the restaurant's slug")
    forget.add_argument("--phone", required=True, help="the customer's number, +92...")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    configure_logging(settings.log_level, fmt=settings.log_format)
    app_url = _url("VELORA_DATABASE_URL")
    if app_url is None:
        print("VELORA_DATABASE_URL must be set", file=sys.stderr)
        return 2
    if args.command == "run":
        dispatcher_url = _url("VELORA_DISPATCHER_DATABASE_URL")
        if dispatcher_url is None:
            print("VELORA_DISPATCHER_DATABASE_URL must be set", file=sys.stderr)
            return 2
        print(f"removed: {asyncio.run(_run(app_url, dispatcher_url), loop_factory=LOOP)}")
        return 0
    platform_url = _url("VELORA_PLATFORM_DATABASE_URL")
    if platform_url is None or not args.operator.strip():
        print("VELORA_PLATFORM_DATABASE_URL and an operator name must be set", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(platform_url) as conn:
            tenant = Onboarding(conn, operator=args.operator).tenant_by_slug(args.tenant)
        report = asyncio.run(
            _forget(app_url, tenant, args.phone, args.operator.strip()), loop_factory=LOOP
        )
    except (OnboardingError, ForgetRefusedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"customer forgotten at {args.tenant}: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
