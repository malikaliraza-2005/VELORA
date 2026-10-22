"""How alerts reach the founder (P10-T3): email and a webhook. Never WhatsApp, which may be
the very thing that is down.

- **Email** over SMTP, with TLS (STARTTLS, or implicit TLS on port 465). Outside development
  and test a server without TLS is refused.
- **Webhook:** a JSON POST, for a push or SMS gateway that reaches the founder's phone. Which
  vendor is a cost decision (PC-11); any service that accepts a JSON POST works.

Network calls use the standard library in a worker thread, so there is no extra dependency.
An error never carries the URL (which may hold a token) or a password.
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal

import anyio.to_thread

from velora.monitoring.config import EmailSettings
from velora.redaction import error_text, register_secret

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0

# (method, url, body or None, timeout) -> HTTP status. Tests replace it.
HttpTransport = Callable[[str, str, bytes | None, float], Awaitable[int]]


class NotifyError(Exception):
    """A notification could not be handed over."""


@dataclass(frozen=True)
class Notification:
    key: str
    severity: Literal["warning", "critical", "info"]
    state: Literal["firing", "resolved", "report"]
    title: str
    body: str


async def http_request(method: str, url: str, body: bytes | None, timeout: float) -> int:
    def call() -> int:
        request = urllib.request.Request(  # noqa: S310 - the URL is checked in config
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "User-Agent": "velora-monitor"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return int(response.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NotifyError(f"request failed: {type(reason).__name__}") from None

    return await anyio.to_thread.run_sync(call)


class Notifier(ABC):
    name: str

    @abstractmethod
    async def send(self, notification: Notification) -> None:
        """Hand the notification over, or raise NotifyError."""


class WebhookNotifier(Notifier):
    name = "webhook"

    def __init__(self, url: str, transport: HttpTransport = http_request) -> None:
        register_secret(url)
        self._url = url
        self._transport = transport

    async def send(self, notification: Notification) -> None:
        body = json.dumps(
            {
                "key": notification.key,
                "severity": notification.severity,
                "state": notification.state,
                "title": notification.title,
                "message": notification.body,
            }
        ).encode()
        status = await self._transport("POST", self._url, body, TIMEOUT_SECONDS)
        if not 200 <= status < 300:
            raise NotifyError(f"webhook answered {status}")


# (settings, message, require TLS) -> None. Tests replace it.
MailTransport = Callable[[EmailSettings, EmailMessage, bool], None]


def smtp_send(settings: EmailSettings, message: EmailMessage, require_tls: bool) -> None:
    context = ssl.create_default_context()
    smtp: smtplib.SMTP
    if settings.port == 465:
        smtp = smtplib.SMTP_SSL(
            settings.host, settings.port, timeout=TIMEOUT_SECONDS, context=context
        )
    else:
        smtp = smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT_SECONDS)
    with smtp:
        if settings.port != 465:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls(context=context)
                smtp.ehlo()
            elif require_tls:
                raise NotifyError("the mail server offers no TLS")
        if settings.username is not None and settings.password is not None:
            smtp.login(settings.username, settings.password)
        smtp.send_message(message)


class EmailNotifier(Notifier):
    name = "email"

    def __init__(
        self, settings: EmailSettings, *, require_tls: bool, transport: MailTransport = smtp_send
    ) -> None:
        if settings.password is not None:
            register_secret(settings.password)
        self._settings = settings
        self._require_tls = require_tls
        self._transport = transport

    async def send(self, notification: Notification) -> None:
        message = EmailMessage()
        message["From"] = self._settings.sender
        message["To"] = ", ".join(self._settings.recipients)
        message["Subject"] = notification.title
        message.set_content(notification.body)
        try:
            await anyio.to_thread.run_sync(
                self._transport, self._settings, message, self._require_tls
            )
        except NotifyError:
            raise
        except (smtplib.SMTPException, OSError) as exc:
            raise NotifyError(error_text(exc)) from None


async def deliver(notifiers: list[Notifier], notification: Notification) -> bool:
    """Send through every notifier. True if at least one accepted it."""
    delivered = False
    for notifier in notifiers:
        try:
            await notifier.send(notification)
        except Exception as exc:
            log.error("alert %s via %s failed: %s", notification.key, notifier.name, exc)
        else:
            delivered = True
    if not notifiers:
        log.warning("no notifier configured; alert %s only logged", notification.key)
    log.log(
        logging.ERROR if notification.severity == "critical" else logging.WARNING,
        "alert %s %s: %s",
        notification.state,
        notification.key,
        notification.title,
    )
    return delivered


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    detail: str


class ReadinessProbe:
    """Asks the API's `/readyz` whether it is up and has its database."""

    def __init__(self, url: str, transport: HttpTransport = http_request) -> None:
        self._url = url
        self._transport = transport

    async def check(self) -> ProbeResult:
        try:
            status = await self._transport("GET", self._url, None, TIMEOUT_SECONDS)
        except Exception as exc:
            return ProbeResult(False, str(exc) if isinstance(exc, NotifyError) else "no answer")
        return ProbeResult(status == 200, f"answered {status}")


class Heartbeat:
    """Pings a dead-man's-switch URL after each monitor cycle."""

    def __init__(self, url: str, transport: HttpTransport = http_request) -> None:
        register_secret(url)
        self._url = url
        self._transport = transport

    async def ping(self) -> bool:
        try:
            status = await self._transport("GET", self._url, None, TIMEOUT_SECONDS)
        except Exception as exc:
            log.warning("heartbeat failed: %s", exc)
            return False
        if not 200 <= status < 300:
            log.warning("heartbeat answered %s", status)
            return False
        return True
