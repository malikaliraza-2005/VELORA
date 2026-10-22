"""P9 dashboard tests in a real browser, against the real app and database, with simulated
WhatsApp (AD-25). Tests that need real WhatsApp traffic stay gated by the OpenWA Go."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import BrowserContext, Page, expect

from tests.web.conftest import Server, Shop, sign_in, wait_until


def test_login_strips_the_token_and_the_browser_stores_no_secrets(
    context: BrowserContext, shop: Shop, server: Server
) -> None:
    """P9-T1: no secrets in the browser beyond the session cookie (automated check)."""
    token = shop.owner_link.split("#", 1)[1]
    page = sign_in(context, shop.owner_link)

    # The token is gone from the address bar, and the session is an HttpOnly cookie.
    assert page.url == f"{server.base_url}/"
    (cookie,) = context.cookies()
    assert cookie["name"] == "__Host-velora_session"
    assert cookie["httpOnly"] and cookie["secure"] and cookie["sameSite"] == "Strict"
    assert page.evaluate("document.cookie") == ""

    # Let the service worker install and fill its cache before looking at it.
    wait_until(page, "navigator.serviceWorker.ready.then(r => Boolean(r.active))")
    page.wait_for_timeout(500)
    stored: dict[str, Any] = page.evaluate(
        """async () => {
            const local = {...localStorage};
            const session = {...sessionStorage};
            const databases = indexedDB.databases ? await indexedDB.databases() : [];
            const cached = [];
            for (const name of await caches.keys()) {
                const cache = await caches.open(name);
                for (const req of await cache.keys()) cached.push(req.url);
            }
            return {local, session, databases, cached};
        }"""
    )
    assert stored["local"] == {} and stored["session"] == {} and stored["databases"] == []
    # The service worker keeps only the app's own files: no API answer, no login token.
    assert not [u for u in stored["cached"] if "/api/" in u or token in u]
    assert token not in page.content()
    assert token not in page.evaluate("JSON.stringify(history.state) + location.href")


def test_every_response_carries_the_security_headers(context: BrowserContext, shop: Shop) -> None:
    page = sign_in(context, shop.owner_link)
    for path in ("/", "/assets/js/main.js", "/api/me"):
        headers = page.request.get(path).headers
        assert "script-src 'self'" in headers["content-security-policy"]
        assert "require-trusted-types-for 'script'" in headers["content-security-policy"]
        assert headers["x-frame-options"] == "DENY"
    assert page.request.get("/api/me").headers["cache-control"] == "no-store"


def _no_console_errors(page: Page) -> list[str]:
    """Script errors and failed requests. Chrome logs every 4xx as a console error, so those
    are recorded by URL instead."""
    errors: list[str] = []
    page.on(
        "console",
        lambda m: (
            errors.append(m.text)
            if m.type == "error" and not m.text.startswith("Failed to load resource")
            else None
        ),
    )
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on(
        "response",
        lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None,
    )
    return errors


def test_every_screen_opens_for_the_owner_without_errors(
    context: BrowserContext, shop: Shop, server: Server
) -> None:
    page = context.new_page()
    errors = _no_console_errors(page)
    page.goto(shop.owner_link)
    page.wait_for_selector("nav.nav a")
    views = page.eval_on_selector_all("nav.nav a", "els => els.map(e => e.dataset.view)")
    assert views == [
        "board",
        "payments",
        "chats",
        "menu",
        "controls",
        "summary",
        "whatsapp",
        "staff",
        "settings",
        "me",
    ]
    for view in views:
        page.click(f"nav.nav a[data-view={view}]")
        expect(page.locator(f"section.view-{view}")).to_be_visible()
        page.wait_for_load_state("networkidle")
    # No menu is published in this restaurant yet; the menu screen says so.
    assert errors == [f"404 {server.base_url}/api/menu/live"], errors
