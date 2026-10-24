"""The founder's monitor: `python -m velora.monitoring <command>` (P10).

Connects as velora_monitor through VELORA_MONITOR_DATABASE_URL. Alerts go by email and/or a
webhook (see `.env.example`), never through WhatsApp.

run                                   the monitor loop: alerts, daily report, heartbeat
status [--json]                       the founder's dashboard: every metric and current alert
daily [--day YYYY-MM-DD] [--json]     pilot metrics per restaurant (default: yesterday, PKT)
record-backup (--ok | --failed) --kind KIND [--detail TEXT]
                                      the backup job (P11) reports each run
test-alert                            send a test notification through every notifier
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from datetime import date, timedelta
from typing import Any

from psycopg import AsyncConnection

from velora.clock import SystemClock
from velora.config import Settings
from velora.conversation.pktime import PKT
from velora.monitoring.alerts import AlertManager, AlertStore, RuleSettings, evaluate
from velora.monitoring.config import MonitorSettings
from velora.monitoring.daily import collect_daily
from velora.monitoring.metrics import collect, record_backup
from velora.monitoring.notify import (
    EmailNotifier,
    Heartbeat,
    Notification,
    Notifier,
    ReadinessProbe,
    WebhookNotifier,
    deliver,
)
from velora.monitoring.report import daily_text, status_text, to_json
from velora.monitoring.runner import Monitor
from velora.redaction import configure_logging, error_text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m velora.monitoring")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    daily = commands.add_parser("daily")
    daily.add_argument("--day", type=date.fromisoformat)
    daily.add_argument("--json", action="store_true")
    backup = commands.add_parser("record-backup")
    outcome = backup.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--ok", action="store_true")
    outcome.add_argument("--failed", action="store_true")
    backup.add_argument("--kind", required=True)
    backup.add_argument("--detail")
    commands.add_parser("test-alert")
    return parser


def notifiers_for(settings: MonitorSettings) -> list[Notifier]:
    found: list[Notifier] = []
    if settings.email is not None:
        found.append(
            EmailNotifier(settings.email, require_tls=settings.env in {"staging", "production"})
        )
    if settings.webhook_url is not None:
        found.append(WebhookNotifier(settings.webhook_url))
    return found


def _rules(settings: MonitorSettings) -> RuleSettings:
    return RuleSettings(settings.expect_backups, settings.backup_max_age, settings.expect_worker)


async def _main(args: argparse.Namespace, settings: MonitorSettings, url: str) -> int:
    clock = SystemClock()

    async def connect() -> AsyncConnection[Any]:
        return await AsyncConnection.connect(url)

    if args.command == "status":
        async with await connect() as conn:
            snapshot = await collect(conn, clock.now())
        alerts = evaluate(
            snapshot, now=snapshot.at, probe_failures=0, probe=None, rules=_rules(settings)
        )
        print(
            to_json({"snapshot": snapshot, "alerts": alerts})
            if args.json
            else status_text(snapshot, alerts)
        )
        return 0
    if args.command == "daily":
        now = clock.now()
        day = args.day or now.astimezone(PKT).date() - timedelta(days=1)
        async with await connect() as conn:
            report = await collect_daily(conn, day, now)
        print(to_json(report) if args.json else daily_text(report))
        return 0
    if args.command == "record-backup":
        async with await connect() as conn:
            await record_backup(
                conn, ok=bool(args.ok), kind=args.kind, detail=args.detail, at=clock.now()
            )
        return 0
    notifiers = notifiers_for(settings)
    if args.command == "test-alert":
        if not notifiers:
            print("no notifier configured", file=sys.stderr)
            return 2
        delivered = await deliver(
            notifiers,
            Notification(
                key="test",
                severity="info",
                state="report",
                title="[VELORA] Test alert",
                body="If you can read this, VELORA alerts reach you.",
            ),
        )
        print("delivered" if delivered else "NOT delivered (see the log)")
        return 0 if delivered else 1
    monitor = Monitor(
        connect,
        clock,
        AlertManager(AlertStore(connect), notifiers),
        _rules(settings),
        notifiers=notifiers,
        probe=None if settings.probe_url is None else ReadinessProbe(settings.probe_url),
        heartbeat=None if settings.heartbeat_url is None else Heartbeat(settings.heartbeat_url),
    )
    stop = asyncio.Event()
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    await monitor.run_forever(stop)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base = Settings.from_env()
    configure_logging(base.log_level, fmt=base.log_format)
    try:
        settings = MonitorSettings.from_env()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if settings.database_url is None:
        print("VELORA_MONITOR_DATABASE_URL must be set", file=sys.stderr)
        return 2
    # psycopg's async mode cannot use the Windows proactor event loop.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with contextlib.suppress(KeyboardInterrupt):
            return asyncio.run(_main(args, settings, settings.database_url), loop_factory=loop)
    except Exception as exc:
        print(f"failed: {error_text(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
