"""P10-T1: structured JSON logs with tenant and correlation ids, and the log redaction test.

Fake secrets are seeded into requests (headers, cookies, query, body), webhooks (a signed
body that fails verification) and errors (exceptions with tracebacks). Every log line must be
JSON, and none of the secrets may appear anywhere in the output.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from velora.app import create_app
from velora.config import Settings
from velora.db import TenantId
from velora.logcontext import log_context
from velora.messaging.provider import InvalidWebhookError
from velora.messaging.providers.fake import SIGNATURE_HEADER, FakeProvider
from velora.redaction import configure_logging

# Fake values only, assembled so no scanner mistakes them for real keys.
BEARER = "tok." + "QmVhcmVyVG9rZW4x"
SESSION_COOKIE = "sess" + "Q29va2llVmFsdWUy"
CSRF = "csrf" + "VG9rZW5WYWx1ZTM0"
API_KEY = "owa_" + "QXBpS2V5VmFsdWU1"
DB_PASSWORD = "pg-" + "UGFzc3dvcmQ2Nzg5"
WEBHOOK_SECRET = "whsec_" + "V2ViaG9va1NlY3I"
MAGIC = "ml." + "TWFnaWNMaW5rMTA"
PHONE = "923001234567"
SECRETS = [BEARER, SESSION_COOKIE, CSRF, API_KEY, DB_PASSWORD, WEBHOOK_SECRET, MAGIC, PHONE]

log = logging.getLogger("velora.test.structured")


@pytest.fixture()
def output() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        configure_logging("INFO", stream)
        yield stream
    finally:
        root.handlers[:], root.level = saved


def lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def leaks(stream: io.StringIO) -> list[str]:
    text = stream.getvalue()
    return [secret for secret in SECRETS if secret in text]


def test_every_line_is_json_with_tenant_and_correlation(output: io.StringIO) -> None:
    tenant = TenantId(uuid.uuid4())
    log.info("outside any context")
    with log_context(tenant=tenant, correlation_id="round-0001"):
        log.warning("inside")
    first, second = lines(output)
    assert set(first) == {"ts", "level", "logger", "msg", "tenant_id", "correlation_id"}
    assert (first["tenant_id"], first["correlation_id"]) == (None, None)
    assert second["tenant_id"] == str(tenant.value)
    assert (second["correlation_id"], second["level"]) == ("round-0001", "WARNING")
    assert second["ts"].endswith("+00:00")


def test_contexts_nest_and_restore(output: io.StringIO) -> None:
    with log_context(correlation_id="outer-0001"):
        with log_context(correlation_id="inner-0002"):
            log.info("a")
        log.info("b")
    assert [line["correlation_id"] for line in lines(output)] == ["inner-0002", "outer-0001"]


def careless_app() -> FastAPI:
    """The real app, plus routes that log carelessly and fail, as future code might."""
    app = create_app(Settings(env="test", log_level="INFO"), web_dir=None)

    @app.post("/test/echo")
    async def echo(request: Request) -> dict[str, str]:
        body = (await request.body()).decode()
        log.info("headers %s url %s body %s", dict(request.headers), request.url, body)
        return {"ok": "yes"}

    @app.get("/test/boom")
    async def boom() -> None:
        raise ConnectionError(
            f"postgresql://app_rw:{DB_PASSWORD}@db:5432/velora refused; api_key={API_KEY}"
        )

    return app


def test_requests_carry_a_correlation_id(output: io.StringIO) -> None:
    client = TestClient(careless_app())
    mine = client.post("/test/echo", headers={"X-Request-ID": "support-ticket-42"})
    theirs = client.post("/test/echo", headers={"X-Request-ID": "bad id with spaces!"})
    assert mine.headers["X-Request-ID"] == "support-ticket-42"
    assert theirs.headers["X-Request-ID"] != "bad id with spaces!"
    assert len(theirs.headers["X-Request-ID"]) == 16
    ids = [line["correlation_id"] for line in lines(output) if line["msg"].startswith("headers")]
    assert ids == ["support-ticket-42", theirs.headers["X-Request-ID"]]


def test_secrets_in_requests_never_reach_the_log(output: io.StringIO) -> None:
    client = TestClient(careless_app())
    response = client.post(
        f"/test/echo?token={MAGIC}&api_key={API_KEY}",
        headers={
            "Authorization": f"Bearer {BEARER}",
            "Cookie": f"__Host-velora_session={SESSION_COOKIE}",
            "X-CSRF-Token": CSRF,
        },
        content=json.dumps({"password": DB_PASSWORD, "phone": f"+{PHONE}"}),
    )
    assert response.status_code == 200
    assert lines(output)
    assert leaks(output) == []


def test_errors_and_tracebacks_never_carry_secrets(output: io.StringIO) -> None:
    client = TestClient(careless_app(), raise_server_exceptions=False)
    response = client.get("/test/boom", headers={"X-Request-ID": "boom-request-1"})
    assert response.status_code == 500
    failed = [line for line in lines(output) if line["msg"] == "GET /test/boom failed"]
    assert failed and failed[0]["correlation_id"] == "boom-request-1"
    assert "Traceback" in failed[0]["exc"]
    assert leaks(output) == []


def test_secrets_in_webhooks_never_reach_the_log(output: io.StringIO) -> None:
    fake = FakeProvider()
    body = json.dumps(
        {"session": "s1", "apiKey": API_KEY, "secret": WEBHOOK_SECRET, "from": f"{PHONE}@c.us"}
    ).encode()
    headers = {SIGNATURE_HEADER: f"sha256={WEBHOOK_SECRET}", "authorization": f"Bearer {BEARER}"}
    with pytest.raises(InvalidWebhookError) as caught:
        fake.parse_webhook("s1", headers, body)
    log.warning("webhook refused: %s headers=%s body=%s", caught.value, headers, body)
    try:
        raise caught.value
    except InvalidWebhookError:
        log.exception("webhook from +%s refused", PHONE)
    assert len(lines(output)) == 2
    assert leaks(output) == []


def test_the_readiness_check_without_a_database_says_unavailable() -> None:
    client = TestClient(create_app(Settings(env="test", log_level="INFO"), web_dir=None))
    assert client.get("/healthz").status_code == 200
    response = client.get("/readyz")
    assert (response.status_code, response.json()) == (503, {"status": "unavailable"})


def test_text_format_is_still_redacted() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        configure_logging("INFO", stream, fmt="text")
        log.info("cookie: __Host-velora_session=%s", SESSION_COOKIE)
    finally:
        root.handlers[:], root.level = saved
    assert SESSION_COOKIE not in stream.getvalue()
    assert not stream.getvalue().startswith("{")
