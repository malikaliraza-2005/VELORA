"""Fixtures for the P3 staff API tests: a real app on the test database, a simulated clock,
founder onboarding through velora_platform, and `Device`s that each hold their own session."""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Iterator
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
from velora.api.deps import CSRF_HEADER, SESSION_COOKIE, ServicesDep, SessionDep
from velora.app import create_app
from velora.clock import FakeClock
from velora.config import Settings
from velora.db import TenantId

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
def app(settings: Settings, clock: FakeClock) -> FastAPI:
    app = create_app(settings, clock=clock)
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


class Device:
    """One browser or tablet: its own session cookie and CSRF token."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.cookie: str | None = None
        self.csrf: str | None = None
        self.staff_id: uuid.UUID | None = None

    def request(
        self, method: str, path: str, *, csrf: bool = True, **kwargs: Any
    ) -> httpx2.Response:
        headers = dict(kwargs.pop("headers", {}))
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


@pytest.fixture()
def restaurant(platform: Onboarding, settings: Settings) -> Restaurant:
    return new_restaurant(platform, settings)


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
