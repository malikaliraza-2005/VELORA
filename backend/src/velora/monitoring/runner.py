"""The monitor loop (P10-T3): one cycle a minute.

Each cycle checks the API's readiness, reads the snapshot, evaluates the rules, lets the
alert manager notify, sends the daily report once a day, and pings the heartbeat. The
monitor is its own process, so a stuck worker or API is seen from outside; the heartbeat
lets an external service notice when the monitor or the whole host stops.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

from psycopg import AsyncConnection

from velora.clock import Clock
from velora.conversation.pktime import PKT
from velora.logcontext import log_context, new_correlation_id
from velora.monitoring.alerts import AlertManager, RuleSettings, evaluate
from velora.monitoring.daily import collect_daily
from velora.monitoring.metrics import Snapshot, collect
from velora.monitoring.notify import (
    Heartbeat,
    Notification,
    Notifier,
    ProbeResult,
    ReadinessProbe,
    deliver,
)
from velora.monitoring.report import daily_text
from velora.redaction import error_text

log = logging.getLogger(__name__)

CYCLE_INTERVAL = timedelta(seconds=60)
# The daily report covers yesterday and goes out at this Pakistan hour.
DAILY_REPORT_HOUR = 9
DAILY_REPORT_RETRY = timedelta(minutes=30)

Connect = Callable[[], Awaitable[AsyncConnection[Any]]]


class Monitor:
    def __init__(
        self,
        connect: Connect,
        clock: Clock,
        manager: AlertManager,
        rules: RuleSettings,
        *,
        notifiers: list[Notifier],
        probe: ReadinessProbe | None = None,
        heartbeat: Heartbeat | None = None,
        daily_report: bool = True,
    ) -> None:
        self._connect = connect
        self._clock = clock
        self._manager = manager
        self._rules = rules
        self._notifiers = notifiers
        self._probe = probe
        self._heartbeat = heartbeat
        self._daily_report = daily_report
        self._probe_failures = 0
        self._reported_day: date | None = None
        self._report_retry_at: datetime | None = None

    async def snapshot(self) -> Snapshot | None:
        try:
            conn = await self._connect()
            async with conn:
                return await collect(conn, self._clock.now())
        except Exception as exc:
            log.error("could not read the metrics: %s", error_text(exc))
            return None

    async def cycle(self) -> list[Notification]:
        with log_context(correlation_id=new_correlation_id()):
            return await self._cycle()

    async def _cycle(self) -> list[Notification]:
        probe: ProbeResult | None = None
        if self._probe is not None:
            probe = await self._probe.check()
            self._probe_failures = 0 if probe.ok else self._probe_failures + 1
            if not probe.ok:
                log.warning("readiness probe failed: %s", probe.detail)
        snapshot = await self.snapshot()
        now = self._clock.now()
        alerts = evaluate(
            snapshot,
            now=now,
            probe_failures=self._probe_failures,
            probe=probe,
            rules=self._rules,
        )
        sent = await self._manager.process(alerts, now)
        if self._daily_report and snapshot is not None:
            report = await self._maybe_report()
            if report is not None:
                sent.append(report)
        if self._heartbeat is not None:
            await self._heartbeat.ping()
        return sent

    async def _maybe_report(self) -> Notification | None:
        now = self._clock.now()
        local = now.astimezone(PKT)
        if local.hour < DAILY_REPORT_HOUR or self._reported_day == local.date():
            return None
        if self._report_retry_at is not None and now < self._report_retry_at:
            return None
        day = local.date() - timedelta(days=1)
        try:
            conn = await self._connect()
            async with conn:
                report = await collect_daily(conn, day, now)
        except Exception as exc:
            log.error("could not build the daily report: %s", error_text(exc))
            return None
        notification = Notification(
            key="daily_report",
            severity="info",
            state="report",
            title=f"[VELORA] Pilot metrics for {day.isoformat()}",
            body=daily_text(report),
        )
        if await deliver(self._notifiers, notification) or not self._notifiers:
            self._reported_day, self._report_retry_at = local.date(), None
        else:
            self._report_retry_at = now + DAILY_REPORT_RETRY
        return notification

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.cycle()
            except Exception as exc:
                log.error("monitor cycle failed: %s", error_text(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), CYCLE_INTERVAL.total_seconds())
