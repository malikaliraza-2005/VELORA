"""P5-T8: secrets never appear in logs (log redaction tested)."""

from __future__ import annotations

import io
import logging

import pytest

from velora.redaction import (
    REDACTED,
    configure_logging,
    error_text,
    redact,
    register_secret,
)

# Fake values only, assembled so no scanner mistakes them for real keys.
API_KEY = "owa_" + "k3y9ZQ2xLp7Rv"
PASSWORD = "pg-" + "S3cretPassw0rd"
SESSION_TOKEN = "tok." + "Zm9vYmFyYmF6cXV4"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"POST /send api_key={API_KEY}", API_KEY),
        (f'{{"X-Api-Key": "{API_KEY}"}}', API_KEY),
        (f"Authorization: Bearer {SESSION_TOKEN}", SESSION_TOKEN),
        (f"x-openwa-signature=sha256:{API_KEY}", API_KEY),
        (f"connect postgresql://app_rw:{PASSWORD}@db:5432/velora", PASSWORD),
        (f"password: {PASSWORD}", PASSWORD),
        (f"login link https://dash.velora.pk/login#{SESSION_TOKEN}", SESSION_TOKEN),
        ("pin=482913 rejected", "482913"),
        ("pepper='abcdefgh12345678'", "abcdefgh12345678"),
    ],
)
def test_secrets_are_redacted(text: str, secret: str) -> None:
    cleaned = redact(text)
    assert secret not in cleaned
    assert REDACTED in cleaned


def test_phone_numbers_and_whatsapp_ids_keep_only_their_last_digits() -> None:
    cleaned = redact("from +923001234567 (923001234567@c.us) about order 12")
    assert "3001234" not in cleaned
    assert cleaned == "from +*********567 (+*********567@c.us) about order 12"


def test_registered_secrets_are_redacted_anywhere() -> None:
    value = "per-session-" + "Xq81LmPz0aaB"
    register_secret(value)
    assert value not in redact(f"weird place:{value}!")
    register_secret("short")  # too short to be scrubbed safely: ignored
    assert redact("a short note") == "a short note"


def test_ordinary_text_is_left_alone() -> None:
    text = "order 12 accepted at 09:30, total Rs 1,050; channel connected"
    assert redact(text) == text


def test_log_records_and_tracebacks_are_redacted() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        configure_logging("INFO", stream)
        log = logging.getLogger("velora.test")
        log.info("calling OpenWA with api_key=%s", API_KEY)
        try:
            raise ConnectionError(f"postgresql://app_rw:{PASSWORD}@db/velora refused")
        except ConnectionError:
            log.exception("send failed for +923001234567")
    finally:
        root.handlers[:], root.level = saved
    output = stream.getvalue()
    for secret in (API_KEY, PASSWORD, "923001234567"):
        assert secret not in output
    assert "Traceback" in output and output.count(REDACTED) >= 2


def test_stored_error_text_is_redacted_and_short() -> None:
    text = error_text(RuntimeError(f"token={SESSION_TOKEN} " + "x" * 500))
    assert SESSION_TOKEN not in text
    assert text.startswith("RuntimeError: token=[REDACTED]")
    assert len(text) == 200
