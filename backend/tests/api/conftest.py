"""Fixtures for the P3 staff API tests: a real app on the test database, a simulated clock,
founder onboarding through velora_platform, and `Device`s that each hold their own session."""

from __future__ import annotations

import asyncio
import functools
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx2
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tests.db import conftest as db_fixtures
from tests.db.conftest import APP_ROLE, PgDatabase
from velora.admin.onboarding import Onboarding
from velora.api import idempotency
from velora.api.deps import CSRF_HEADER, SESSION_COOKIE, Services, ServicesDep, SessionDep
from velora.app import create_app
from velora.clock import FakeClock
from velora.config import Settings
from velora.db import TenantId
from velora.messaging.providers.fake import FakeProvider

# The database fixtures live in tests/db; re-export them for the API tests.
cluster = db_fixtures.cluster
db = db_fixtures.db

BASE_URL = "https://dash.velora.test"


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 21, 9, 0, tzinfo=UTC))


@pytest.fixture()
def settings(db: PgDatabase) -> Settings:
    return Settings(
        env="test",
        log_level="INFO",
        database_url=db.cluster.url(db.name, APP_ROLE),
        public_base_url=BASE_URL,
    )


class PinConfirm(BaseModel):
    pin: str
    confirmer_id: uuid.UUID | None = None


def add_payment_pin_probe(app: FastAPI) -> None:
    """Stands in for P8's payment confirmation, which must call verify_payment_pin first."""

    @app.post("/test/payments/confirm")
    async def confirm(
        body: PinConfirm, session: SessionDep, services: ServicesDep
    ) -> dict[str, str]:
        confirmer = await services.staff.verify_payment_pin(
            session, pin=body.pin, confirmer_id=body.confirmer_id
        )
        return {"confirmed_by": str(confirmer)}


@pytest.fixture()
def fake() -> FakeProvider:
    """Simulated WhatsApp for the API (the owner's QR re-link, P9)."""
    return FakeProvider()


@pytest.fixture()
def app(settings: Settings, clock: FakeClock, fake: FakeProvider) -> FastAPI:
    app = create_app(settings, clock=clock, providers={"fake": fake})
    add_payment_pin_probe(app)
    return app


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        # psycopg's async mode cannot use the Windows proactor event loop.
        options["loop_factory"] = asyncio.SelectorEventLoop
    with TestClient(app, base_url=BASE_URL, backend_options=options) as test_client:
        yield test_client


@pytest.fixture()
def platform(db: PgDatabase, clock: FakeClock) -> Iterator[Onboarding]:
    with db.platform() as conn:
        yield Onboarding(conn, operator="test-founder", clock=clock)


_UNSET: Any = object()


class Device:
    """One browser or tablet: its own session cookie and CSRF token. Like the dashboard, it
    sends a fresh Idempotency-Key with every state-changing request unless given one (None
    sends none)."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.cookie: str | None = None
        self.csrf: str | None = None
        self.staff_id: uuid.UUID | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        csrf: bool = True,
        idempotency_key: str | None = _UNSET,
        **kwargs: Any,
    ) -> httpx2.Response:
        headers = dict(kwargs.pop("headers", {}))
        if idempotency_key is _UNSET and method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            idempotency_key = uuid.uuid4().hex
        if isinstance(idempotency_key, str):
            headers[idempotency.HEADER] = idempotency_key
        if self.cookie is not None:
            headers["Cookie"] = f"{SESSION_COOKIE}={self.cookie}"
        if csrf and self.csrf is not None:
            headers[CSRF_HEADER] = self.csrf
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, **kwargs)
        self.client.cookies.clear()
        return response

    def login(self, link_url: str) -> httpx2.Response:
        response = self.request("POST", "/api/auth/login", json={"token": token_of(link_url)})
        if response.status_code == 200:
            self.cookie = response.cookies[SESSION_COOKIE]
            self.csrf = response.json()["csrf_token"]
            self.staff_id = uuid.UUID(response.json()["staff_id"])
        return response


def token_of(link_url: str) -> str:
    assert link_url.startswith(f"{BASE_URL}/login#")
    return link_url.split("#", 1)[1]


@dataclass
class Restaurant:
    tenant: TenantId
    owner_link: str
    owner_id: uuid.UUID


def new_restaurant(platform: Onboarding, settings: Settings) -> Restaurant:
    slug = f"r-{uuid.uuid4().hex[:12]}"
    tenant = platform.create_tenant(slug=slug, display_name=f"Test {slug}", order_prefix="TR")
    invite = platform.invite_owner(tenant, display_name="Owner")
    return Restaurant(tenant, settings.magic_link_url(invite.token.wire()), invite.staff_id)


def connect_channel(db: PgDatabase, platform: Onboarding, tenant: TenantId) -> uuid.UUID:
    """Register a simulated WhatsApp channel and mark it connected (P5 circuit breaker)."""
    channel = platform.register_channel(
        tenant, provider="fake", session_ref=f"session-{tenant.value}"
    )
    with db.admin() as conn:
        conn.execute("UPDATE velora.channels SET state = 'connected' WHERE id = %s", (channel,))
    return channel


@pytest.fixture()
def restaurant(db: PgDatabase, platform: Onboarding, settings: Settings) -> Restaurant:
    found = new_restaurant(platform, settings)
    connect_channel(db, platform, found.tenant)
    return found


@pytest.fixture()
def owner(client: TestClient, restaurant: Restaurant) -> Device:
    device = Device(client)
    assert device.login(restaurant.owner_link).status_code == 200
    return device


def invite(owner: Device, role: str, name: str | None = None) -> tuple[uuid.UUID, str]:
    response = owner.request(
        "POST", "/api/staff", json={"display_name": name or f"{role} person", "role": role}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return uuid.UUID(body["staff"]["id"]), body["link"]["url"]


def join(client: TestClient, owner: Device, role: str) -> Device:
    """Invite a staff member with `role` and sign them in on a new device."""
    _, link = invite(owner, role)
    device = Device(client)
    assert device.login(link).status_code == 200
    return device


def audit_actions(db: PgDatabase, tenant: TenantId) -> list[str]:
    with db.admin() as conn:
        return [r[0] for r in audit_rows(conn, tenant)]


def audit_rows(conn: psycopg.Connection[Any], tenant: TenantId) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT action, actor_kind, actor_id, target_id, details FROM velora.audit_log "
        "WHERE tenant_id = %s ORDER BY created_at, action",
        (tenant.value,),
    ).fetchall()


# Menus (P4) -------------------------------------------------------------------------------

SAMPLE_CSV = """kind,category,sku,name,price,description,group,min,max
item,Burgers,ZINGER,Zinger Burger,650,Crispy chicken fillet,,,
option,,ZINGER,Regular,0,,Size,1,1
option,,ZINGER,Large,150,,Size,,
item,Burgers,SMASH,Beef Smash Burger,890,,,,
item,Drinks,COKE,Coca-Cola 345ml,120,,,,
"""


def sample_menu(zinger_paisa: int = 65000, *, extra_sku: str | None = None) -> dict[str, Any]:
    """The JSON form of SAMPLE_CSV, optionally with a changed price or an extra item."""
    burgers: list[dict[str, Any]] = [
        {
            "sku": "ZINGER",
            "name": "Zinger Burger",
            "description": "Crispy chicken fillet",
            "price_paisa": zinger_paisa,
            "groups": [
                {
                    "name": "Size",
                    "min_select": 1,
                    "max_select": 1,
                    "options": [
                        {"name": "Regular", "price_delta_paisa": 0},
                        {"name": "Large", "price_delta_paisa": 15000},
                    ],
                }
            ],
        },
        {"sku": "SMASH", "name": "Beef Smash Burger", "price_paisa": 89000},
    ]
    if extra_sku is not None:
        burgers.append({"sku": extra_sku, "name": f"Item {extra_sku}", "price_paisa": 50000})
    drinks = [{"sku": "COKE", "name": "Coca-Cola 345ml", "price_paisa": 12000}]
    return {
        "categories": [{"name": "Burgers", "items": burgers}, {"name": "Drinks", "items": drinks}]
    }


def save_draft(device: Device, menu: dict[str, Any] | None = None) -> dict[str, Any]:
    """Save `menu` as the draft, starting from whatever revision the draft is at."""
    base = device.request("GET", "/api/menu/draft").json()["base_revision"]
    response = device.request(
        "PUT", "/api/menu/draft", json={"base_revision": base, "menu": menu or sample_menu()}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def publish(device: Device, version: dict[str, Any]) -> httpx2.Response:
    return device.request(
        "POST",
        f"/api/menu/versions/{version['id']}/publish",
        json={"revision": version["revision"]},
    )


def publish_menu(device: Device, menu: dict[str, Any] | None = None) -> dict[str, Any]:
    """Save `menu` as the draft and publish it. Returns the published version."""
    response = publish(device, save_draft(device, menu))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def services_of(client: TestClient) -> Services:
    services = client.app.state.services  # type: ignore[attr-defined]
    assert isinstance(services, Services)
    return services


def run_async[T](
    client: TestClient, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any
) -> T:
    """Run a service coroutine on the app's event loop (where its connection pool lives)."""
    assert client.portal is not None
    return client.portal.call(functools.partial(fn, *args, **kwargs))
