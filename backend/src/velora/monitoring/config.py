"""Monitor settings, from environment variables (P10).

Real values live only in the environment; `.env.example` holds placeholders. Outside
development and test the monitor refuses to start without a way to reach the founder (an
email or a webhook), and only over encrypted connections.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit

from velora.config import PLACEHOLDER

DEFAULT_SMTP_PORT = 587
DEFAULT_BACKUP_MAX_AGE = timedelta(minutes=120)


def _optional(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name, "").strip()
    return None if not value or value == PLACEHOLDER else value


def _url(source: Mapping[str, str], name: str, *, https: bool) -> str | None:
    value = _optional(source, name)
    if value is None:
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"{name} must be an http(s) URL")
    if https and parts.scheme != "https":
        raise ValueError(f"{name} must use https in staging and production")
    return value


@dataclass(frozen=True)
class EmailSettings:
    host: str
    port: int
    sender: str
    recipients: tuple[str, ...]
    username: str | None = None
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class MonitorSettings:
    env: str
    # velora_monitor. Needed by every command.
    database_url: str | None = field(default=None, repr=False)
    # The API's readiness URL (`/readyz`), checked every cycle: the "ingress down" alert.
    probe_url: str | None = None
    # Pinged after every monitor cycle. An external service (e.g. a cron-job monitor) alerts
    # when the pings stop, so a dead host or a dead monitor is noticed too.
    heartbeat_url: str | None = field(default=None, repr=False)
    # Alerts as a JSON POST, e.g. to a push or SMS gateway that reaches the founder's phone.
    webhook_url: str | None = field(default=None, repr=False)
    email: EmailSettings | None = None
    # Backups are expected (P11) from staging on; before that, their absence isn't alerted.
    expect_backups: bool = False
    # A worker is expected from staging on; its silence is then alerted (AD-38).
    expect_worker: bool = False
    backup_max_age: timedelta = DEFAULT_BACKUP_MAX_AGE

    @property
    def has_notifier(self) -> bool:
        return self.webhook_url is not None or self.email is not None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MonitorSettings:
        source = os.environ if environ is None else environ
        env = source.get("VELORA_ENV", "development").strip().lower()
        deployed = env in {"staging", "production"}
        email = None
        recipients = _optional(source, "VELORA_ALERT_EMAIL_TO")
        if recipients is not None:
            host = _optional(source, "VELORA_SMTP_HOST")
            sender = _optional(source, "VELORA_SMTP_FROM")
            if host is None or sender is None:
                raise ValueError(
                    "VELORA_ALERT_EMAIL_TO needs VELORA_SMTP_HOST and VELORA_SMTP_FROM"
                )
            port_text = _optional(source, "VELORA_SMTP_PORT")
            try:
                port = DEFAULT_SMTP_PORT if port_text is None else int(port_text)
            except ValueError:
                port = 0
            if not 1 <= port <= 65535:
                raise ValueError("VELORA_SMTP_PORT must be a port number")
            email = EmailSettings(
                host=host,
                port=port,
                sender=sender,
                recipients=tuple(r.strip() for r in recipients.split(",") if r.strip()),
                username=_optional(source, "VELORA_SMTP_USERNAME"),
                password=_optional(source, "VELORA_SMTP_PASSWORD"),
            )
        expect = _optional(source, "VELORA_MONITOR_EXPECT_BACKUPS")
        if expect is not None and expect.lower() not in {"true", "false"}:
            raise ValueError("VELORA_MONITOR_EXPECT_BACKUPS must be true or false")
        worker = _optional(source, "VELORA_MONITOR_EXPECT_WORKER")
        if worker is not None and worker.lower() not in {"true", "false"}:
            raise ValueError("VELORA_MONITOR_EXPECT_WORKER must be true or false")
        age_text = _optional(source, "VELORA_BACKUP_MAX_AGE_MINUTES")
        try:
            age = DEFAULT_BACKUP_MAX_AGE if age_text is None else timedelta(minutes=int(age_text))
        except ValueError:
            age = timedelta(0)
        if not timedelta(minutes=15) <= age <= timedelta(days=2):
            raise ValueError("VELORA_BACKUP_MAX_AGE_MINUTES must be from 15 to 2880")
        settings = cls(
            env=env,
            database_url=_optional(source, "VELORA_MONITOR_DATABASE_URL"),
            # The probe runs on the private network, so plain http is allowed.
            probe_url=_url(source, "VELORA_MONITOR_PROBE_URL", https=False),
            heartbeat_url=_url(source, "VELORA_MONITOR_HEARTBEAT_URL", https=deployed),
            webhook_url=_url(source, "VELORA_ALERT_WEBHOOK_URL", https=deployed),
            email=email,
            expect_backups=deployed if expect is None else expect.lower() == "true",
            expect_worker=deployed if worker is None else worker.lower() == "true",
            backup_max_age=age,
        )
        if deployed and not settings.has_notifier:
            raise ValueError(
                "set VELORA_ALERT_EMAIL_TO or VELORA_ALERT_WEBHOOK_URL: alerts must reach the "
                "founder in staging and production"
            )
        return settings
