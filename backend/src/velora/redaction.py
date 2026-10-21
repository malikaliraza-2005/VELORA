"""Log redaction (P5-T8, AD-24): secrets never reach logs or stored error text.

`redact` scrubs:
- `key=value` and `key: value` pairs whose key names a secret (api key, token, password,
  signature, authorization, PIN, pepper, ...), and bearer tokens;
- passwords inside connection URLs;
- magic-link tokens (`/login#...`);
- values registered with `register_secret`, wherever they appear;
- phone numbers and WhatsApp ids, down to their last three digits (data minimisation).

`configure_logging` installs a formatter that applies it to every formatted record, tracebacks
included. Error text the queues store goes through `redact` as well.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from typing import TextIO

REDACTED = "[REDACTED]"

# Shorter values would match too much ordinary text.
MIN_SECRET_LENGTH = 8

_SENSITIVE_NAMES = (
    r"(?:x-)?api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|client[-_]?secret"
    r"|password|passwd|pwd|authorization|signature|x-[a-z-]*signature|pin|pepper|hmac"
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


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str, stream: TextIO | None = None) -> logging.Handler:
    """Send every log record through the redacting formatter. Returns the handler."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    return handler
