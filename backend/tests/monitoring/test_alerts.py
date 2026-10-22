"""P10-T3: alert rules, the alert manager (no noise, nothing missed), the notifiers and the
monitor settings. No database: the manager runs without a store here."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import timedelta
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tests.monitoring.conftest import T0, RecordingNotifier
from velora.monitoring.alerts import (
    REMIND_EVERY,
    RETRY_UNDELIVERED,
    Alert,
    AlertManager,
    RuleSettings,
    evaluate,
)
from velora.monitoring.config import EmailSettings, MonitorSettings
from velora.monitoring.metrics import BackupRun, ChannelHealth, Snapshot
from velora.monitoring.notify import (
    EmailNotifier,
    Heartbeat,
    Notification,
    NotifyError,
    ReadinessProbe,
    WebhookNotifier,
)

RULES = RuleSettings(expect_backups=False, backup_max_age=timedelta(hours=2))
CHANNEL = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


def keys(snapshot: Snapshot | None, **kwargs: Any) -> list[str]:
    options: dict[str, Any] = {"now": T0, "probe_failures": 0, "probe": None, "rules": RULES}
    options.update(kwargs)
    return [a.key for a in evaluate(snapshot, **options)]


def channel(state: str, since_minutes: int) -> ChannelHealth:
    return ChannelHealth(
        uuid.uuid4(), "karachi-grill", CHANNEL, state, T0 - timedelta(minutes=since_minutes)
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# Rules ------------------------------------------------------------------------------------


def test_a_healthy_deployment_raises_nothing() -> None:
    assert keys(Snapshot(at=T0, channels=(channel("connected", 600),))) == []


def test_the_api_is_down_after_two_failed_probes() -> None:
    snapshot = Snapshot(at=T0)
    assert keys(snapshot, probe_failures=1) == []
    assert keys(snapshot, probe_failures=2) == ["api_down"]


def test_an_unreadable_database_is_an_alert() -> None:
    assert keys(None) == ["monitor_database"]


@pytest.mark.parametrize("state", ["needs_qr", "logged_out", "failed", "reconnecting"])
def test_a_channel_down_for_five_minutes_is_critical(state: str) -> None:
    assert keys(Snapshot(at=T0, channels=(channel(state, 4),))) == []
    alerts = evaluate(
        Snapshot(at=T0, channels=(channel(state, 5),)),
        now=T0,
        probe_failures=0,
        probe=None,
        rules=RULES,
    )
    assert [(a.key, a.severity) for a in alerts] == [(f"channel_down:{CHANNEL}", "critical")]
    assert "karachi-grill" in alerts[0].summary and "5 min" in alerts[0].summary


def test_a_suspected_restriction_is_a_stop_rule_at_once() -> None:
    (alert,) = evaluate(
        Snapshot(at=T0, channels=(channel("suspected_restricted", 0),)),
        now=T0,
        probe_failures=0,
        probe=None,
        rules=RULES,
    )
    assert alert.key == f"channel_restricted:{CHANNEL}"
    assert "AD-22" in alert.summary


def test_queue_backlogs_after_five_minutes() -> None:
    snapshot = Snapshot(
        at=T0,
        inbox_due=3,
        inbox_oldest_due=timedelta(minutes=5),
        outbox_due=1,
        outbox_oldest_due=timedelta(minutes=4),
    )
    assert keys(snapshot) == ["inbox_backlog"]


def test_dead_letters_carry_their_count_as_level() -> None:
    (alert,) = evaluate(
        Snapshot(at=T0, inbox_failed=1, outbox_failed=2, outbox_unknown=1),
        now=T0,
        probe_failures=0,
        probe=None,
        rules=RULES,
    )
    assert (alert.key, alert.level) == ("dead_letters", 4)


def test_refused_webhooks_alert_from_five_in_fifteen_minutes() -> None:
    assert keys(Snapshot(at=T0, ingress_rejections={"signature": 4})) == []
    assert keys(Snapshot(at=T0, ingress_rejections={"signature": 3, "payload": 2})) == [
        "ingress_rejections"
    ]


def test_backups() -> None:
    failed = BackupRun(False, "hourly-dump", T0, "disk full")
    good = BackupRun(True, "hourly-dump", T0, None)
    assert keys(Snapshot(at=T0, last_backup=failed)) == ["backup_failed"]
    expecting = RuleSettings(expect_backups=True, backup_max_age=timedelta(hours=2))
    assert keys(Snapshot(at=T0), rules=expecting) == ["backup_stale"]
    fresh = Snapshot(at=T0, last_backup=good, last_good_backup=T0 - timedelta(minutes=90))
    assert keys(fresh, rules=expecting) == []
    old = Snapshot(at=T0, last_backup=good, last_good_backup=T0 - timedelta(minutes=121))
    assert keys(old, rules=expecting) == ["backup_stale"]


# Manager ----------------------------------------------------------------------------------


def manager() -> tuple[AlertManager, RecordingNotifier]:
    notifier = RecordingNotifier()
    return AlertManager(None, [notifier]), notifier


def test_an_alert_is_sent_once_then_reminded_then_resolved() -> None:
    mgr, notifier = manager()
    problem = [Alert("inbox_backlog", "warning", "3 waiting")]
    run(mgr.process(problem, T0))
    run(mgr.process(problem, T0 + timedelta(minutes=1)))
    run(mgr.process(problem, T0 + timedelta(minutes=2)))
    assert notifier.keys() == ["inbox_backlog"]
    run(mgr.process(problem, T0 + REMIND_EVERY))
    assert notifier.keys() == ["inbox_backlog", "inbox_backlog"]
    assert "Still:" in notifier.sent[-1].title
    run(mgr.process([], T0 + REMIND_EVERY + timedelta(minutes=1)))
    run(mgr.process([], T0 + REMIND_EVERY + timedelta(minutes=2)))
    assert notifier.keys("resolved") == ["inbox_backlog"]


def test_a_growing_problem_is_sent_again() -> None:
    mgr, notifier = manager()
    run(mgr.process([Alert("dead_letters", "warning", "2", level=2)], T0))
    run(mgr.process([Alert("dead_letters", "warning", "2", level=2)], T0 + timedelta(minutes=1)))
    run(mgr.process([Alert("dead_letters", "warning", "5", level=5)], T0 + timedelta(minutes=2)))
    assert [n.title for n in notifier.sent] == [
        "[VELORA WARNING] 2",
        "[VELORA WARNING] 5",
    ]


def test_an_undelivered_alert_is_tried_again() -> None:
    mgr, notifier = manager()
    notifier.down = True
    problem = [Alert("backup_failed", "critical", "disk full")]
    run(mgr.process(problem, T0))
    notifier.down = False
    run(mgr.process(problem, T0 + timedelta(minutes=1)))
    assert notifier.sent == []
    run(mgr.process(problem, T0 + RETRY_UNDELIVERED))
    assert notifier.keys() == ["backup_failed"]
    assert "Still:" not in notifier.sent[0].title


def test_each_notifier_is_tried_even_when_one_fails() -> None:
    broken, working = RecordingNotifier(), RecordingNotifier()
    broken.down = True
    mgr = AlertManager(None, [broken, working])
    sent = run(mgr.process([Alert("api_down", "critical", "down")], T0))
    assert [n.key for n in sent] == ["api_down"]
    assert working.keys() == ["api_down"]


# Notifiers --------------------------------------------------------------------------------

NOTE = Notification("api_down", "critical", "firing", "[VELORA CRITICAL] down", "details")
HOOK = "https://alerts.example.test/hook/" + "s3cr3t-token-value"


def test_the_webhook_posts_json() -> None:
    calls: list[tuple[str, str, bytes | None]] = []

    async def transport(method: str, url: str, body: bytes | None, timeout: float) -> int:
        calls.append((method, url, body))
        return 202

    run(WebhookNotifier(HOOK, transport).send(NOTE))
    ((method, url, body),) = calls
    assert (method, url) == ("POST", HOOK)
    assert json.loads(body or b"") == {
        "key": "api_down",
        "severity": "critical",
        "state": "firing",
        "title": "[VELORA CRITICAL] down",
        "message": "details",
    }


def test_a_webhook_refusal_is_an_error_without_the_url() -> None:
    async def transport(method: str, url: str, body: bytes | None, timeout: float) -> int:
        return 500

    with pytest.raises(NotifyError) as caught:
        run(WebhookNotifier(HOOK, transport).send(NOTE))
    assert "s3cr3t" not in str(caught.value)


def test_email_goes_to_every_recipient() -> None:
    sent: list[tuple[EmailMessage, bool]] = []

    def transport(settings: EmailSettings, message: EmailMessage, require_tls: bool) -> None:
        sent.append((message, require_tls))

    settings = EmailSettings("smtp.example.test", 587, "alerts@velora.test", ("a@x.pk", "b@x.pk"))
    run(EmailNotifier(settings, require_tls=True, transport=transport).send(NOTE))
    ((message, require_tls),) = sent
    assert message["To"] == "a@x.pk, b@x.pk"
    assert message["Subject"] == "[VELORA CRITICAL] down"
    assert require_tls


def test_email_failures_become_notify_errors() -> None:
    def transport(settings: EmailSettings, message: EmailMessage, require_tls: bool) -> None:
        raise ConnectionRefusedError("connect password=hunter2hunter2 refused")

    settings = EmailSettings("smtp.example.test", 587, "a@velora.test", ("a@x.pk",))
    with pytest.raises(NotifyError) as caught:
        run(EmailNotifier(settings, require_tls=True, transport=transport).send(NOTE))
    assert "hunter2" not in str(caught.value)


def test_the_probe_and_heartbeat() -> None:
    answers: list[int | Exception] = [200, 503, NotifyError("request failed: timeout")]

    async def transport(method: str, url: str, body: bytes | None, timeout: float) -> int:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    probe = ReadinessProbe("http://api:8000/readyz", transport)
    results = [run(probe.check()) for _ in range(3)]
    assert [r.ok for r in results] == [True, False, False]
    assert results[2].detail == "request failed: timeout"
    answers[:] = [200, 404]
    beat = Heartbeat("https://hc.example.test/ping/" + "abcdefgh1234", transport)
    assert run(beat.ping()) and not run(beat.ping())


# Settings ---------------------------------------------------------------------------------


def test_deployed_monitors_must_reach_the_founder() -> None:
    with pytest.raises(ValueError, match="alerts must reach the founder"):
        MonitorSettings.from_env({"VELORA_ENV": "production"})
    settings = MonitorSettings.from_env(
        {"VELORA_ENV": "production", "VELORA_ALERT_WEBHOOK_URL": HOOK}
    )
    assert settings.has_notifier and settings.expect_backups


def test_deployed_alert_urls_must_use_https() -> None:
    with pytest.raises(ValueError, match="https"):
        MonitorSettings.from_env(
            {"VELORA_ENV": "staging", "VELORA_ALERT_WEBHOOK_URL": "http://alerts.test/x"}
        )


def test_email_settings_need_a_server_and_sender() -> None:
    with pytest.raises(ValueError, match="SMTP"):
        MonitorSettings.from_env({"VELORA_ALERT_EMAIL_TO": "a@x.pk"})
    settings = MonitorSettings.from_env(
        {
            "VELORA_ALERT_EMAIL_TO": "a@x.pk, b@x.pk",
            "VELORA_SMTP_HOST": "smtp.example.test",
            "VELORA_SMTP_FROM": "alerts@velora.test",
            "VELORA_SMTP_PASSWORD": "not-in-repr-123",
        }
    )
    assert settings.email is not None
    assert settings.email.recipients == ("a@x.pk", "b@x.pk")
    assert settings.email.port == 587
    assert "not-in-repr" not in repr(settings)
    assert not settings.expect_backups


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VELORA_BACKUP_MAX_AGE_MINUTES", "5"),
        ("VELORA_MONITOR_EXPECT_BACKUPS", "yes"),
        ("VELORA_MONITOR_PROBE_URL", "ftp://api/readyz"),
    ],
)
def test_bad_settings_are_refused(name: str, value: str) -> None:
    with pytest.raises(ValueError):
        MonitorSettings.from_env({name: value})


def test_real_http_webhook_probe_and_heartbeat() -> None:
    received: list[tuple[str, str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server's naming
            length = int(self.headers["Content-Length"])
            received.append(("POST", self.path, self.rfile.read(length)))
            self.send_response(204)
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            received.append(("GET", self.path, b""))
            self.send_response(503 if self.path == "/other" else 200)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        run(WebhookNotifier(f"{base}/hook/abcdefgh").send(NOTE))
        ready = run(ReadinessProbe(f"{base}/readyz").check())
        not_ready = run(ReadinessProbe(f"{base}/other").check())
        beat = run(Heartbeat(f"{base}/ping/abcdefgh").ping())
    finally:
        server.shutdown()
        server.server_close()
    closed = run(ReadinessProbe(f"{base}/readyz").check())
    assert received[0][:2] == ("POST", "/hook/abcdefgh")
    assert json.loads(received[0][2])["key"] == "api_down"
    assert (ready.ok, not_ready.ok, not_ready.detail, beat) == (True, False, "answered 503", True)
    assert not closed.ok
