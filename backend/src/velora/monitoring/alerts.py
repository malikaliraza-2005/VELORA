"""Alert rules and the alert manager (P10-T3).

**Rules** (`evaluate`) turn a snapshot and the readiness probe into the problems present now:

| Key | Fires when | Severity |
|---|---|---|
| `api_down` | the API's `/readyz` failed twice in a row | critical |
| `monitor_database` | the monitor can't read the database | critical |
| `channel_restricted:<channel>` | `suspected_restricted` (stop rule, AD-22) | critical |
| `channel_down:<channel>` | a channel has been out of `connected` for 5 minutes | critical |
| `inbox_backlog` / `outbox_backlog` | the oldest due item has waited 5 minutes | warning |
| `dead_letters` | failed inbox events or failed/unknown messages in 24 hours | warning |
| `ingress_rejections` | 5 or more refused webhooks in 15 minutes | warning |
| `backup_failed` | the latest backup run failed | critical |
| `backup_stale` | no good backup within the allowed age (once backups are expected) | critical |
| `worker_down` | no worker heartbeat for 3 minutes (once a worker is expected) | critical |

**The manager** sends each problem once when it starts, again when it grows (its `level`, e.g.
the number of dead letters) or every 4 hours while it lasts, and once when it clears. Each
notification is appended to `alert_events`, which is also how a restarted monitor knows what
it already sent. An alert no notifier accepted is tried again after 5 minutes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection

from velora.monitoring.metrics import Snapshot
from velora.monitoring.notify import Notification, Notifier, ProbeResult, deliver
from velora.redaction import error_text

log = logging.getLogger(__name__)

CHANNEL_DOWN_AFTER = timedelta(minutes=5)
QUEUE_AGE_LIMIT = timedelta(minutes=5)
INGRESS_REJECTION_LIMIT = 5
PROBE_FAILURES_LIMIT = 2
REMIND_EVERY = timedelta(hours=4)
RETRY_UNDELIVERED = timedelta(minutes=5)
# The worker beats every 15 seconds (P11); a restart takes seconds.
WORKER_SILENT_AFTER = timedelta(minutes=3)

Severity = Literal["warning", "critical"]


@dataclass(frozen=True)
class Alert:
    key: str
    severity: Severity
    summary: str
    level: int = 0


@dataclass(frozen=True)
class RuleSettings:
    expect_backups: bool
    backup_max_age: timedelta
    # A worker is expected (staging and production): alert when its heartbeat is too old.
    expect_worker: bool = False


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def evaluate(
    snapshot: Snapshot | None,
    *,
    now: datetime,
    probe_failures: int,
    probe: ProbeResult | None,
    rules: RuleSettings,
) -> list[Alert]:
    """The problems present now. `snapshot` is None when the database couldn't be read."""
    alerts: list[Alert] = []
    if probe_failures >= PROBE_FAILURES_LIMIT:
        detail = probe.detail if probe is not None else "no answer"
        alerts.append(Alert("api_down", "critical", f"The API is not ready ({detail})."))
    if snapshot is None:
        alerts.append(
            Alert("monitor_database", "critical", "The monitor cannot read the database.")
        )
        return alerts
    for ch in snapshot.channels:
        if ch.state in {"connected", "pending"}:
            continue
        if ch.state == "suspected_restricted":
            alerts.append(
                Alert(
                    f"channel_restricted:{ch.channel_id}",
                    "critical",
                    f"{ch.tenant}: WhatsApp number may be restricted. Stop rule (AD-22): "
                    "pause onboarding and reassess.",
                )
            )
            continue
        down_for = now - ch.since if ch.since is not None else CHANNEL_DOWN_AFTER
        if down_for >= CHANNEL_DOWN_AFTER:
            alerts.append(
                Alert(
                    f"channel_down:{ch.channel_id}",
                    "critical",
                    f"{ch.tenant}: WhatsApp is {ch.state} for {_minutes(down_for)} min; "
                    "orders are closed.",
                )
            )
    for name, age, count in (
        ("inbox", snapshot.inbox_oldest_due, snapshot.inbox_due),
        ("outbox", snapshot.outbox_oldest_due, snapshot.outbox_due),
    ):
        if age is not None and age >= QUEUE_AGE_LIMIT:
            alerts.append(
                Alert(
                    f"{name}_backlog",
                    "warning",
                    f"{count} {name} item(s) waiting; the oldest for {_minutes(age)} min. "
                    "Is the worker running?",
                )
            )
    if snapshot.dead_letters:
        alerts.append(
            Alert(
                "dead_letters",
                "warning",
                f"In 24 hours: {snapshot.inbox_failed} inbox event(s) failed, "
                f"{snapshot.outbox_failed} message(s) failed, {snapshot.outbox_unknown} with "
                "unknown delivery. Check the restaurants' Chats screens.",
                level=snapshot.dead_letters,
            )
        )
    rejected = sum(snapshot.ingress_rejections.values())
    if rejected >= INGRESS_REJECTION_LIMIT:
        reasons = ", ".join(f"{k} {v}" for k, v in sorted(snapshot.ingress_rejections.items()))
        alerts.append(
            Alert(
                "ingress_rejections",
                "warning",
                f"{rejected} webhooks refused in 15 min ({reasons}). A wrong secret, or "
                "forged requests.",
                level=rejected,
            )
        )
    last = snapshot.last_backup
    if last is not None and not last.ok:
        alerts.append(
            Alert(
                "backup_failed",
                "critical",
                f"The {last.kind} backup failed" + (f": {last.detail}" if last.detail else "."),
            )
        )
    if rules.expect_worker:
        seen = snapshot.worker_last_seen
        if seen is None or now - seen > WORKER_SILENT_AFTER:
            since = "has never run" if seen is None else f"silent for {_minutes(now - seen)} min"
            alerts.append(
                Alert("worker_down", "critical", f"The worker {since}: no messages are handled.")
            )
    if rules.expect_backups:
        good = snapshot.last_good_backup
        if good is None or now - good > rules.backup_max_age:
            since = "ever" if good is None else f"for {_minutes(now - good)} min"
            alerts.append(Alert("backup_stale", "critical", f"No good backup {since}."))
    return alerts


@dataclass
class _Sent:
    severity: Severity
    level: int
    summary: str
    at: datetime
    delivered: bool


class AlertStore:
    """`alert_events`, through a `velora_monitor` connection factory."""

    def __init__(self, connect: Callable[[], Awaitable[AsyncConnection[Any]]]) -> None:
        self._connect = connect

    async def open_alerts(self) -> dict[str, _Sent]:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (alert_key) alert_key, state, severity, level, summary, "
                "created_at, delivered FROM velora.alert_events ORDER BY alert_key, seq DESC"
            )
            rows = await cur.fetchall()
        return {
            key: _Sent(severity, level, summary, at, delivered)
            for key, state, severity, level, summary, at, delivered in rows
            if state == "firing"
        }

    async def append(
        self, alert: Alert, state: Literal["firing", "resolved"], delivered: bool, at: datetime
    ) -> None:
        conn = await self._connect()
        async with conn, conn.transaction():
            await conn.execute(
                "INSERT INTO velora.alert_events "
                "(alert_key, severity, state, level, summary, delivered, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (alert.key, alert.severity, state, alert.level, alert.summary[:300], delivered, at),
            )


class AlertManager:
    def __init__(self, store: AlertStore | None, notifiers: list[Notifier]) -> None:
        self._store = store
        self._notifiers = notifiers
        self._open: dict[str, _Sent] | None = None

    async def _load(self) -> dict[str, _Sent]:
        if self._open is None:
            loaded: dict[str, _Sent] = {}
            if self._store is not None:
                try:
                    loaded = await self._store.open_alerts()
                except Exception as exc:
                    # Start from nothing: open problems are sent again once, not every cycle.
                    log.error("could not read earlier alerts: %s", error_text(exc))
            self._open = loaded
        return self._open

    async def process(self, alerts: list[Alert], now: datetime) -> list[Notification]:
        """Notify what is new, grown, due a reminder or cleared. Returns what was sent."""
        open_ = await self._load()
        sent: list[Notification] = []
        current = {a.key: a for a in alerts}
        for alert in alerts:
            before = open_.get(alert.key)
            due = (
                before is None
                or alert.level > before.level
                or (not before.delivered and now - before.at >= RETRY_UNDELIVERED)
                or now - before.at >= REMIND_EVERY
            )
            if not due:
                continue
            reminder = before is not None and alert.level <= before.level and before.delivered
            title = f"[VELORA {alert.severity.upper()}] {alert.summary}"[:150]
            if reminder:
                title = f"[VELORA {alert.severity.upper()}] Still: {alert.summary}"[:150]
            sent.append(await self._send(alert, "firing", title, now, open_))
        for key in [k for k in open_ if k not in current]:
            before = open_[key]
            alert = Alert(key, before.severity, f"Resolved: {before.summary}", before.level)
            sent.append(
                await self._send(
                    alert, "resolved", f"[VELORA resolved] {before.summary}"[:150], now, open_
                )
            )
        return sent

    async def _send(
        self,
        alert: Alert,
        state: Literal["firing", "resolved"],
        title: str,
        now: datetime,
        open_: dict[str, _Sent],
    ) -> Notification:
        notification = Notification(
            key=alert.key,
            severity=alert.severity,
            state=state,
            title=title,
            body=f"{alert.summary}\n\nAt {now.isoformat(timespec='seconds')} ({alert.key}).",
        )
        delivered = await deliver(self._notifiers, notification)
        if state == "firing":
            open_[alert.key] = _Sent(alert.severity, alert.level, alert.summary, now, delivered)
        else:
            open_.pop(alert.key, None)
        if self._store is not None:
            try:
                await self._store.append(alert, state, delivered, now)
            except Exception as exc:
                log.error("could not record alert %s: %s", alert.key, error_text(exc))
        return notification
