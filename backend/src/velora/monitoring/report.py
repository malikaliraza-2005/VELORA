"""Plain-text and JSON renderings of the snapshot and the daily report, for the founder's
command line and the daily email (P10-T4)."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta
from typing import Any

from velora.conversation.pktime import PKT
from velora.monitoring.alerts import Alert
from velora.monitoring.daily import DailyReport
from velora.monitoring.metrics import Snapshot


def _age(delta: timedelta | None) -> str:
    if delta is None:
        return "-"
    seconds = int(delta.total_seconds())
    return f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s"


def _when(at: datetime | None) -> str:
    return "never" if at is None else at.astimezone(PKT).strftime("%Y-%m-%d %H:%M PKT")


def status_text(snapshot: Snapshot, alerts: list[Alert]) -> str:
    s = snapshot
    f = s.funnel
    lines = [
        f"VELORA status at {_when(s.at)}",
        "",
        f"Global pause:      {s.global_pause or 'off'}",
        f"Inbox:             {s.inbox_due} due, oldest {_age(s.inbox_oldest_due)}; "
        f"{s.inbox_failed} failed in 24 h",
        f"Outbox:            {s.outbox_due} due, oldest {_age(s.outbox_oldest_due)}; "
        f"{s.outbox_waiting_for_channel} waiting for a channel; "
        f"{s.outbox_failed} failed and {s.outbox_unknown} unknown in 24 h",
        f"Channel changes:   {s.channel_changes} in 24 h",
        "Refused webhooks:  "
        + (
            ", ".join(f"{k} {v}" for k, v in sorted(s.ingress_rejections.items()))
            or "none in 15 min"
        ),
        "Last backup:       "
        + (
            "none recorded"
            if s.last_backup is None
            else f"{'ok' if s.last_backup.ok else 'FAILED'} ({s.last_backup.kind}) "
            f"at {_when(s.last_backup.at)}"
        )
        + f"; last good {_when(s.last_good_backup)}",
        f"Orders today:      {f.placed} placed: {f.awaiting} awaiting, {f.in_progress} in "
        f"progress, {f.completed} completed, {f.cancelled} cancelled or rejected, "
        f"{f.expired} expired, {f.refund_pending} refund pending",
        "",
        "Channels:",
    ]
    if not s.channels:
        lines.append("  (none)")
    for ch in s.channels:
        since = "" if ch.since is None else f" since {_when(ch.since)}"
        lines.append(f"  {ch.tenant:<24} {ch.state:<20}{since}  [{ch.channel_id}]")
    lines += ["", "Alerts now:"]
    if not alerts:
        lines.append("  none")
    for alert in alerts:
        lines.append(f"  {alert.severity.upper():<8} {alert.key}: {alert.summary}")
    return "\n".join(lines)


DAILY_COLUMNS = (
    ("orders", "Orders"),
    ("completed", "Done"),
    ("completed_without_handoff", "No human"),
    ("cancelled", "Cancel"),
    ("expired", "Expired"),
    ("handoffs", "Handoffs"),
    ("disconnects", "Disconn"),
    ("needs_qr_minutes", "QR min"),
    ("payments_verified", "Paid"),
    ("payments_reversed", "Reversed"),
    ("dead_letters", "Dead"),
)


def daily_text(report: DailyReport) -> str:
    header = f"{'Restaurant':<24}" + "".join(f"{title:>10}" for _, title in DAILY_COLUMNS)
    lines = [f"VELORA pilot metrics for {report.day.isoformat()} (Pakistan time)", "", header]
    for row in report.tenants:
        lines.append(
            f"{row.tenant[:24]:<24}"
            + "".join(f"{getattr(row, name):>10}" for name, _ in DAILY_COLUMNS)
        )
    if not report.tenants:
        lines.append("(no restaurants)")
    lines += [
        "",
        f"Problems alerted (all restaurants): {report.alerts}",
        "No human = completed with no handoff to staff that day before the order.",
        "Incidents are recorded by hand (P13).",
    ]
    return "\n".join(lines)


def _plain(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    return str(value)


def to_json(value: Any) -> str:
    """A dataclass (or a dict of them) as JSON."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    return json.dumps(value, default=_plain, indent=2)
