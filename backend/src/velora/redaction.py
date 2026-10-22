"""Log redaction (P5-T8, AD-24): secrets never reach logs or stored error text.

`redact` scrubs:
- `key=value` and `key: value` pairs whose key names a secret (api key, token, password,
  signature, authorization, PIN, pepper, cookie, session cookie, ...), and bearer tokens;
- passwords inside connection URLs;
- magic-link tokens (`/login#...`);
- values registered with `register_secret`, wherever they appear;
- phone numbers and WhatsApp ids, down to their last three digits (data minimisation).

`configure_logging` installs a formatter that applies it to every formatted record, tracebacks
included. Error text the queues store goes through `redact` as well.

Logs are structured (P10-T1): one JSON object per line, with the tenant and correlation id
from `velora.logcontext`. Every string in it is redacted before it is written. `text` format
(one readable line) is for local development.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import UTC, datetime
from typing import Literal, TextIO

from velora.logcontext import current_correlation_id, current_tenant

REDACTED = "[REDACTED]"

# Shorter values would match too much ordinary text.
MIN_SECRET_LENGTH = 8

_SENSITIVE_NAMES = (
    r"(?:x-)?api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|client[-_]?secret"
    r"|password|passwd|pwd|authorization|signature|x-[a-z-]*signature|pin|pepper|hmac"
    # Session cookies (P10): a logged Cookie header or the dashboard's session cookie.
    r"|cookie|set-cookie|[\w-]*_session|session[-_]?(?:id|token|key)"
)
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), rf"\1 {REDACTED}"),
    (
        re.compile(rf"(?i)(\b(?:{_SENSITIVE_NAMES})\b[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}}]+"),
        rf"\1{REDACTED}",
    ),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/@\s]+):[^@\s]+@"), rf"\1:{REDACTED}@"),
    (re.compile(r"(/login#)[A-Za-z0-9._~-]+"), rf"\1{REDACTED}"),
]
_PHONE = re.compile(r"(?<![\w+])\+?(\d{7,12})(\d{3})(?=@(?:c\.us|s\.whatsapp\.net)\b|\b)")

_registered: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str) -> None:
    """Scrub this exact value from all later output, e.g. a per-session API key."""
    if len(value) >= MIN_SECRET_LENGTH:
        with _lock:
            _registered.add(value)


def _mask_phone(match: re.Match[str]) -> str:
    return "+" + "*" * len(match[1]) + match[2]


def redact(text: str) -> str:
    with _lock:
        known = sorted(_registered, key=len, reverse=True)
    for value in known:
        text = text.replace(value, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return _PHONE.sub(_mask_phone, text)


def error_text(exc: BaseException, limit: int = 200) -> str:
    """A short, redacted description of an error, safe to store and show to staff."""
    return redact(f"{type(exc).__name__}: {exc}")[:limit]


LogFormat = Literal["json", "text"]

# Servers that install their own handlers; their records go through ours instead.
_SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    """One redacted JSON object per record."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, str | None] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "tenant_id": current_tenant(),
            "correlation_id": current_correlation_id(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        cleaned = {key: None if value is None else redact(value) for key, value in entry.items()}
        return json.dumps(cleaned, ensure_ascii=False)


def configure_logging(
    level: str, stream: TextIO | None = None, fmt: LogFormat = "json"
) -> logging.Handler:
    """Send every log record through a redacting formatter. Returns the handler."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(
        JsonFormatter()
        if fmt == "json"
        else RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    for name in _SERVER_LOGGERS:
        server = logging.getLogger(name)
        for existing in list(server.handlers):
            server.removeHandler(existing)
        server.propagate = True
    return handler
