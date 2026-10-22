"""Fixtures for the dashboard browser tests (P9): the real app served by uvicorn on the test
database, a simulated WhatsApp (`FakeProvider`) shared with the test, and headless Chromium.

Without Chromium the tests are skipped, unless VELORA_REQUIRE_BROWSER_TESTS=1 (as in CI), in
which case they fail. Install it with `uv run playwright install chromium`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import socket
import sys
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, expect, sync_playwright
from psycopg_pool import AsyncConnectionPool

from tests.api.conftest import connect_channel, new_restaurant
from tests.db import conftest as db_fixtures
from tests.db.conftest import APP_ROLE, PgDatabase
from velora.admin.onboarding import Onboarding
from velora.app import LOGIN_RATE_WINDOW, create_app
from velora.clock import SystemClock
from velora.config import Settings
from velora.db import Database, TenantId
from velora.identity.ratelimit import RateLimiter
from velora.messaging.providers.fake import FakeProvider

cluster = db_fixtures.cluster
db = db_fixtures.db

# Polls run every 5 seconds; allow two rounds and some slack.
UPDATE_TIMEOUT_MS = 15_000
expect.set_options(timeout=UPDATE_TIMEOUT_MS)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@dataclass
class Server:
    base_url: str
    settings: Settings
    provider: FakeProvider


@pytest.fixture(scope="session")
def server(db: PgDatabase) -> Iterator[Server]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    settings = Settings(
        env="test",
        log_level="WARNING",
        database_url=db.cluster.url(db.name, APP_ROLE),
        public_base_url=base_url,
    )
    provider = FakeProvider()
    app = create_app(settings, providers={"fake": provider})
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    uv = uvicorn.Server(config)
    # psycopg's async mode cannot use the Windows proactor event loop.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    thread = threading.Thread(
        target=lambda: asyncio.run(uv.serve(), loop_factory=loop), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 20
    while not uv.started:
        if time.monotonic() > deadline or not thread.is_alive():
            pytest.fail("the dashboard server did not start")
        time.sleep(0.05)
    # Every test signs in from 127.0.0.1, which would soon hit the login rate limit.
    services = app.state.services
    app.state.services = dataclasses.replace(
        services, login_limiter=RateLimiter(10_000, LOGIN_RATE_WINDOW, SystemClock())
    )
    try:
        yield Server(base_url, settings, provider)
    finally:
        uv.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="session")
def playwright() -> Iterator[Playwright]:
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright: Playwright) -> Iterator[Browser]:
    try:
        found = playwright.chromium.launch()
    except Exception as exc:
        if os.environ.get("VELORA_REQUIRE_BROWSER_TESTS") == "1":
            pytest.fail(f"VELORA_REQUIRE_BROWSER_TESTS=1 but Chromium can't start: {exc}")
        pytest.skip(f"Chromium not available ({exc}); run `uv run playwright install chromium`")
    yield found
    found.close()


@pytest.fixture()
def context(browser: Browser, server: Server) -> Iterator[BrowserContext]:
    ctx = browser.new_context(base_url=server.base_url, viewport={"width": 1280, "height": 900})
    ctx.set_default_timeout(UPDATE_TIMEOUT_MS)
    yield ctx
    ctx.close()


@pytest.fixture()
def platform(db: PgDatabase) -> Iterator[Onboarding]:
    with db.platform() as conn:
        yield Onboarding(conn, operator="test-founder")


@dataclass
class Shop:
    tenant: TenantId
    owner_link: str
    owner_id: uuid.UUID
    channel_id: uuid.UUID
    session_ref: str


@pytest.fixture()
def shop(db: PgDatabase, platform: Onboarding, server: Server) -> Shop:
    restaurant = new_restaurant(platform, server.settings)
    channel = connect_channel(db, platform, restaurant.tenant)
    return Shop(
        restaurant.tenant,
        restaurant.owner_link,
        restaurant.owner_id,
        channel,
        f"session-{restaurant.tenant.value}",
    )


def sign_in(context: BrowserContext, link: str) -> Page:
    """Open a magic link and wait for the dashboard."""
    page = context.new_page()
    page.goto(link)
    page.wait_for_selector("nav.nav a")
    return page


def api(page: Page, method: str, path: str, body: Any = None) -> Any:
    """Call the API as the page's signed-in session, the way the dashboard does."""
    me = page.request.get("/api/me").json()
    response = page.request.fetch(
        path,
        method=method,
        headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": uuid.uuid4().hex},
        data=body,
    )
    assert response.ok, f"{method} {path}: {response.status} {response.text()}"
    return response.json() if response.status != 204 else None


def invite_and_sign_in(
    browser_context: BrowserContext, owner: Page, role: str, name: str
) -> tuple[Page, uuid.UUID]:
    found = api(owner, "POST", "/api/staff", {"display_name": name, "role": role})
    page = sign_in(browser_context, found["link"]["url"])
    return page, uuid.UUID(found["staff"]["id"])


def run_async[T](db: PgDatabase, fn: Callable[[Database], Awaitable[T]]) -> T:
    """Run worker-side code (outbox sender, health poll) against the test database."""

    async def main() -> T:
        async with AsyncConnectionPool(db.cluster.url(db.name, APP_ROLE), open=False) as pool:
            return await fn(Database(pool))

    # Sync Playwright already runs an event loop on this thread.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, main(), loop_factory=loop).result()


def wait_until(page: Page, script: str, timeout_ms: int = UPDATE_TIMEOUT_MS) -> None:
    """Poll a script until it is true. (`page.wait_for_function` evaluates a string inside
    the page, which the dashboard's CSP rightly refuses.)"""
    deadline = time.monotonic() + timeout_ms / 1000
    while not page.evaluate(script):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for: {script}")
        page.wait_for_timeout(200)
