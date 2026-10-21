"""P3-T6: every privileged endpoint writes an audit row (actor, tenant, action, time).

`test_every_state_changing_endpoint_has_an_audit_case` fails when a new endpoint is added
without a case here, so coverage can't silently drop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import (
    SAMPLE_CSV,
    Device,
    Restaurant,
    join,
    publish,
    publish_menu,
    sample_menu,
    save_draft,
)
from tests.db.conftest import PgDatabase
from tests.db.seed import insert_order
from velora.app import create_app
from velora.config import Settings
from velora.db import TenantId


@dataclass
class World:
    client: TestClient
    owner: Device
    target: Device  # a kitchen member, signed in
    db: PgDatabase
    tenant: TenantId


Case = Callable[[World], tuple[Device, httpx2.Response]]


def _login(w: World) -> tuple[Device, httpx2.Response]:
    url = w.owner.request("POST", f"/api/staff/{w.target.staff_id}/login-link").json()["url"]
    device = Device(w.client)
    response = device.login(url)
    return device, response


def _on_target(method: str, suffix: str, **kwargs: object) -> Case:
    def case(w: World) -> tuple[Device, httpx2.Response]:
        return w.owner, w.owner.request(method, f"/api/staff/{w.target.staff_id}{suffix}", **kwargs)

    return case


def _publish(w: World) -> tuple[Device, httpx2.Response]:
    return w.owner, publish(w.owner, save_draft(w.owner))


def _availability(w: World) -> tuple[Device, httpx2.Response]:
    publish_menu(w.owner)
    return w.owner, w.owner.request(
        "PUT", "/api/menu/availability/ZINGER", json={"available": False}
    )


def _resume(w: World) -> tuple[Device, httpx2.Response]:
    w.owner.request("POST", "/api/ordering/pause", json={"minutes": 20})
    return w.owner, w.owner.request("POST", "/api/ordering/resume")


def _resume_bot(w: World) -> tuple[Device, httpx2.Response]:
    """P6: a chat handed over to staff, then Resume bot."""
    with w.db.admin() as conn, conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(w.tenant.value),))
        row = conn.execute(
            "INSERT INTO velora.customers (tenant_id, phone_e164) VALUES (%s, '+923000000555') "
            "RETURNING id",
            (w.tenant.value,),
        ).fetchone()
        assert row is not None
        found = conn.execute(
            "INSERT INTO velora.conversations (tenant_id, channel_id, customer_id, state, "
            "handoff_at, handoff_reason) SELECT %s, id, %s, 'handoff', now(), 'requested' "
            "FROM velora.channels WHERE tenant_id = %s RETURNING id",
            (w.tenant.value, row[0], w.tenant.value),
        ).fetchone()
        assert found is not None
    return w.owner, w.owner.request("POST", f"/api/conversations/{found[0]}/resume")


def _move_order(w: World) -> tuple[Device, httpx2.Response]:
    """P7: staff accept a cash-on-delivery order."""
    with w.db.admin() as conn:
        order = insert_order(conn, w.tenant.value)
    return w.owner, w.owner.request(
        "POST", f"/api/orders/{order}/status", json={"from": "cod_confirmed", "to": "accepted"}
    )


CASES: dict[tuple[str, str], tuple[str, Case]] = {
    ("POST", "/api/auth/login"): ("auth.login", _login),
    ("POST", "/api/auth/logout"): (
        "auth.logout",
        lambda w: (w.target, w.target.request("POST", "/api/auth/logout")),
    ),
    ("POST", "/api/auth/logout-all"): (
        "auth.logout_all",
        lambda w: (w.target, w.target.request("POST", "/api/auth/logout-all")),
    ),
    ("PUT", "/api/me/pin"): (
        "staff.pin_set",
        lambda w: (w.target, w.target.request("PUT", "/api/me/pin", json={"new_pin": "480135"})),
    ),
    ("POST", "/api/staff"): (
        "staff.invited",
        lambda w: (
            w.owner,
            w.owner.request("POST", "/api/staff", json={"display_name": "N", "role": "kitchen"}),
        ),
    ),
    ("POST", "/api/staff/{staff_id}/login-link"): (
        "staff.login_link_issued",
        _on_target("POST", "/login-link"),
    ),
    ("PATCH", "/api/staff/{staff_id}"): (
        "staff.role_changed",
        _on_target("PATCH", "", json={"role": "manager"}),
    ),
    ("DELETE", "/api/staff/{staff_id}"): ("staff.removed", _on_target("DELETE", "")),
    ("POST", "/api/staff/{staff_id}/revoke-sessions"): (
        "staff.sessions_revoked",
        _on_target("POST", "/revoke-sessions"),
    ),
    ("POST", "/api/staff/{staff_id}/pin-reset"): (
        "staff.pin_reset",
        _on_target("POST", "/pin-reset"),
    ),
    ("PATCH", "/api/settings"): (
        "settings.updated",
        lambda w: (w.owner, w.owner.request("PATCH", "/api/settings", json={"display_name": "N"})),
    ),
    # P4: menu and ordering controls.
    ("PUT", "/api/menu/draft"): (
        "menu.draft_saved",
        lambda w: (
            w.owner,
            w.owner.request(
                "PUT", "/api/menu/draft", json={"base_revision": 0, "menu": sample_menu()}
            ),
        ),
    ),
    ("POST", "/api/menu/draft/import"): (
        "menu.draft_imported",
        lambda w: (
            w.owner,
            w.owner.request(
                "POST", "/api/menu/draft/import", json={"base_revision": 0, "csv": SAMPLE_CSV}
            ),
        ),
    ),
    ("POST", "/api/menu/versions/{version_id}/publish"): ("menu.published", _publish),
    ("PUT", "/api/menu/availability/{sku}"): ("menu.availability_changed", _availability),
    ("PATCH", "/api/settings/ordering"): (
        "settings.ordering_updated",
        lambda w: (
            w.owner,
            w.owner.request("PATCH", "/api/settings/ordering", json={"delivery_fee_paisa": 15000}),
        ),
    ),
    ("POST", "/api/ordering/pause"): (
        "ordering.paused",
        lambda w: (w.owner, w.owner.request("POST", "/api/ordering/pause", json={"minutes": 20})),
    ),
    ("POST", "/api/ordering/resume"): ("ordering.resumed", _resume),
    # P6: human handoff.
    ("POST", "/api/conversations/{conversation_id}/resume"): (
        "conversation.bot_resumed",
        _resume_bot,
    ),
    # P7: order lifecycle.
    ("POST", "/api/orders/{order_id}/status"): ("order.status_changed", _move_order),
}


def test_every_state_changing_endpoint_has_an_audit_case() -> None:
    # The OpenAPI schema lists every route, including those of included routers.
    paths = create_app(Settings(env="test", log_level="INFO")).openapi()["paths"]
    routes = {
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}
    }
    assert routes == set(CASES)


@pytest.mark.parametrize("key", sorted(CASES), ids=lambda k: f"{k[0]} {k[1]}")
def test_privileged_endpoint_writes_an_audit_row(
    key: tuple[str, str],
    client: TestClient,
    owner: Device,
    restaurant: Restaurant,
    db: PgDatabase,
) -> None:
    action, run = CASES[key]
    world = World(client, owner, join(client, owner, "kitchen"), db, restaurant.tenant)
    with db.admin() as conn:
        before = {r[0] for r in _audit(conn, restaurant)}

    actor, response = run(world)
    assert response.status_code < 300, response.text

    with db.admin() as conn:
        new = [r for r in _audit(conn, restaurant) if r[0] not in before and r[1] == action]
    assert len(new) == 1, f"expected one new {action} audit row, got {new}"
    _, _, actor_kind, actor_id, created_at = new[0]
    assert (actor_kind, actor_id) == ("staff", actor.staff_id)
    assert created_at is not None


def _audit(conn: psycopg.Connection[Any], restaurant: Restaurant) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT id, action, actor_kind, actor_id, created_at FROM velora.audit_log "
        "WHERE tenant_id = %s",
        (restaurant.tenant.value,),
    ).fetchall()
