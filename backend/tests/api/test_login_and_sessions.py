"""P3-T2 magic-link login and P3-T3 device sessions, end to end against PostgreSQL.

Exit criteria covered here: a reused or expired link fails; a revoked session gets a 401 on
its next request; cross-tenant dashboard access is denied.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi.testclient import TestClient

from tests.api.conftest import (
    Device,
    Restaurant,
    audit_actions,
    audit_rows,
    invite,
    join,
    new_restaurant,
    token_of,
)
from tests.db.conftest import PgDatabase
from velora.admin.onboarding import Onboarding
from velora.api.deps import SESSION_COOKIE
from velora.app import LOGIN_RATE_LIMIT
from velora.clock import FakeClock
from velora.config import Settings
from velora.identity.tokens import parse_token

# Magic links (T2) -------------------------------------------------------------------------


def test_owner_link_signs_in_with_a_hardened_cookie(
    client: TestClient, restaurant: Restaurant
) -> None:
    device = Device(client)
    response = device.login(restaurant.owner_link)
    assert response.status_code == 200
    assert response.json()["role"] == "owner"
    assert device.staff_id == restaurant.owner_id

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{SESSION_COOKIE}=")
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert flag.lower() in set_cookie.lower()
    assert "Domain" not in set_cookie  # required by the __Host- prefix

    assert device.request("GET", "/api/me").json()["staff_id"] == str(restaurant.owner_id)


def test_a_reused_link_fails(client: TestClient, restaurant: Restaurant, db: PgDatabase) -> None:
    assert Device(client).login(restaurant.owner_link).status_code == 200
    second = Device(client)
    assert second.login(restaurant.owner_link).status_code == 401
    assert second.cookie is None
    with db.admin() as conn:
        failed = [r for r in audit_rows(conn, restaurant.tenant) if r[0] == "auth.login_failed"]
    assert [r[4]["reason"] for r in failed] == ["reused"]


def test_an_expired_link_fails(
    client: TestClient, restaurant: Restaurant, clock: FakeClock, db: PgDatabase
) -> None:
    clock.advance(timedelta(minutes=15))
    assert Device(client).login(restaurant.owner_link).status_code == 401
    with db.admin() as conn:
        failed = [r for r in audit_rows(conn, restaurant.tenant) if r[0] == "auth.login_failed"]
    assert [r[4]["reason"] for r in failed] == ["expired"]


def test_a_link_still_works_just_before_expiry(
    client: TestClient, restaurant: Restaurant, clock: FakeClock
) -> None:
    clock.advance(timedelta(minutes=14, seconds=59))
    assert Device(client).login(restaurant.owner_link).status_code == 200


def test_a_link_logs_in_only_the_person_it_was_issued_for(
    client: TestClient, owner: Device
) -> None:
    kitchen_id, link = invite(owner, "kitchen", "Ali")
    device = Device(client)
    body = device.login(link).json()
    assert (body["staff_id"], body["role"], body["display_name"]) == (
        str(kitchen_id),
        "kitchen",
        "Ali",
    )


def test_a_link_with_another_tenant_swapped_in_fails(
    client: TestClient, restaurant: Restaurant, platform: Onboarding, settings: Settings
) -> None:
    other = new_restaurant(platform, settings)
    token = parse_token(token_of(restaurant.owner_link))
    assert token is not None
    forged = f"{other.tenant.value.hex}.{token.wire().split('.', 1)[1]}"
    response = Device(client).request("POST", "/api/auth/login", json={"token": forged})
    assert response.status_code == 401
    # The genuine link was not consumed by the forgery.
    assert Device(client).login(restaurant.owner_link).status_code == 200


def test_malformed_and_unknown_tokens_fail(client: TestClient, restaurant: Restaurant) -> None:
    unknown = f"{restaurant.tenant.value.hex}.{'A' * 43}"
    for token in ["", "garbage", unknown]:
        response = Device(client).request("POST", "/api/auth/login", json={"token": token})
        assert response.status_code == 401


def test_login_requests_above_the_rate_limit_are_refused(
    client: TestClient, restaurant: Restaurant, clock: FakeClock
) -> None:
    device = Device(client)
    for _ in range(LOGIN_RATE_LIMIT):
        assert device.request("POST", "/api/auth/login", json={"token": "x"}).status_code == 401
    # Even a valid link is refused while the limit is exhausted, and it is not consumed.
    assert device.login(restaurant.owner_link).status_code == 429
    clock.advance(timedelta(minutes=5, seconds=1))
    assert device.login(restaurant.owner_link).status_code == 200


def test_login_from_another_origin_is_refused(client: TestClient, restaurant: Restaurant) -> None:
    response = Device(client).request(
        "POST",
        "/api/auth/login",
        json={"token": token_of(restaurant.owner_link)},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_sessions_and_links_are_stored_only_as_hashes(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert owner.cookie is not None
    session = parse_token(owner.cookie)
    link = parse_token(token_of(restaurant.owner_link))
    assert session is not None and link is not None
    with db.admin() as conn:
        stored_session = conn.execute(
            "SELECT count(*) FROM velora.staff_sessions WHERE token_hash = %s",
            (hashlib.sha256(session.secret).digest(),),
        ).fetchone()
        stored_link = conn.execute(
            "SELECT count(*) FROM velora.magic_links WHERE token_hash = %s",
            (hashlib.sha256(link.secret).digest(),),
        ).fetchone()
    assert stored_session == (1,)
    assert stored_link == (1,)


# Device sessions (T3) ---------------------------------------------------------------------


def test_a_logged_out_session_gets_401(
    owner: Device, db: PgDatabase, restaurant: Restaurant
) -> None:
    assert owner.request("POST", "/api/auth/logout").status_code == 204
    assert owner.request("GET", "/api/me").status_code == 401
    assert "auth.logout" in audit_actions(db, restaurant.tenant)


def test_log_out_all_devices(client: TestClient, owner: Device) -> None:
    staff_id, _ = invite(owner, "manager")
    phone, tablet = Device(client), Device(client)
    for device in (phone, tablet):
        response = owner.request("POST", f"/api/staff/{staff_id}/login-link")
        assert device.login(response.json()["url"]).status_code == 200

    response = phone.request("POST", "/api/auth/logout-all")
    assert response.json() == {"sessions_revoked": 2}
    assert phone.request("GET", "/api/me").status_code == 401
    assert tablet.request("GET", "/api/me").status_code == 401
    assert owner.request("GET", "/api/me").status_code == 200


def test_owner_revocation_gets_401_on_the_next_request(client: TestClient, owner: Device) -> None:
    kitchen = join(client, owner, "kitchen")
    assert kitchen.request("GET", "/api/me").status_code == 200
    response = owner.request("POST", f"/api/staff/{kitchen.staff_id}/revoke-sessions")
    assert response.json() == {"sessions_revoked": 1}
    assert kitchen.request("GET", "/api/me").status_code == 401


def test_removing_a_staff_member_revokes_their_sessions_and_links(
    client: TestClient, owner: Device
) -> None:
    kitchen = join(client, owner, "kitchen")
    unused = owner.request("POST", f"/api/staff/{kitchen.staff_id}/login-link").json()["url"]
    assert owner.request("DELETE", f"/api/staff/{kitchen.staff_id}").status_code == 204
    assert kitchen.request("GET", "/api/me").status_code == 401
    assert Device(client).login(unused).status_code == 401
    response = owner.request("POST", f"/api/staff/{kitchen.staff_id}/login-link")
    assert response.status_code == 409


def test_a_role_change_revokes_sessions(client: TestClient, owner: Device) -> None:
    manager = join(client, owner, "manager")
    response = owner.request("PATCH", f"/api/staff/{manager.staff_id}", json={"role": "kitchen"})
    assert response.status_code == 200
    assert response.json()["role"] == "kitchen"
    assert manager.request("GET", "/api/me").status_code == 401


def test_idle_expiry_after_12_hours(owner: Device, clock: FakeClock) -> None:
    clock.advance(timedelta(hours=11, minutes=59))
    assert owner.request("GET", "/api/me").status_code == 200  # activity resets the idle timer
    clock.advance(timedelta(hours=11, minutes=59))
    assert owner.request("GET", "/api/me").status_code == 200
    clock.advance(timedelta(hours=12))
    assert owner.request("GET", "/api/me").status_code == 401
    clock.advance(timedelta(minutes=1))
    assert owner.request("GET", "/api/me").status_code == 401  # stays revoked


def test_absolute_expiry_after_30_days_despite_activity(owner: Device, clock: FakeClock) -> None:
    elapsed = timedelta()
    while elapsed + timedelta(hours=11) < timedelta(days=30):
        clock.advance(timedelta(hours=11))
        elapsed += timedelta(hours=11)
        assert owner.request("GET", "/api/me").status_code == 200
    clock.advance(timedelta(days=30) - elapsed)
    assert owner.request("GET", "/api/me").status_code == 401


def test_expired_sessions_are_recorded_as_revoked(
    owner: Device, clock: FakeClock, db: PgDatabase, restaurant: Restaurant
) -> None:
    clock.advance(timedelta(hours=12))
    assert owner.request("GET", "/api/me").status_code == 401
    with db.admin() as conn:
        rows = conn.execute(
            "SELECT revoked_reason FROM velora.staff_sessions WHERE tenant_id = %s",
            (restaurant.tenant.value,),
        ).fetchall()
    assert rows == [("expired_idle",)]


def test_requests_without_a_session_get_401(client: TestClient) -> None:
    device = Device(client)
    assert device.request("GET", "/api/me").status_code == 401
    device.cookie = "garbage"
    assert device.request("GET", "/api/me").status_code == 401


# CSRF -------------------------------------------------------------------------------------


def test_state_changes_need_the_csrf_token(owner: Device) -> None:
    body = {"display_name": "Renamed"}
    assert owner.request("PATCH", "/api/settings", json=body, csrf=False).status_code == 403
    response = owner.request(
        "PATCH", "/api/settings", json=body, csrf=False, headers={"X-CSRF-Token": "wrong"}
    )
    assert response.status_code == 403
    assert owner.request("POST", "/api/auth/logout", csrf=False).status_code == 403
    assert owner.request("PATCH", "/api/settings", json=body).status_code == 204


def test_state_changes_from_another_origin_are_refused(owner: Device) -> None:
    response = owner.request(
        "PATCH",
        "/api/settings",
        json={"display_name": "Renamed"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_csrf_token_of_another_session_is_refused(client: TestClient, owner: Device) -> None:
    other = join(client, owner, "manager")
    owner.csrf, other.csrf = other.csrf, owner.csrf
    assert owner.request("PATCH", "/api/settings", json={"display_name": "X"}).status_code == 403


# Cross-tenant -----------------------------------------------------------------------------


def test_cross_tenant_dashboard_access_is_denied(
    client: TestClient,
    owner: Device,
    restaurant: Restaurant,
    platform: Onboarding,
    settings: Settings,
) -> None:
    other = new_restaurant(platform, settings)
    other_owner = Device(client)
    assert other_owner.login(other.owner_link).status_code == 200
    victim_id, _ = invite(other_owner, "kitchen")

    # A's owner sees only A's staff, and can't touch B's staff by id.
    listed = {s["id"] for s in owner.request("GET", "/api/staff").json()}
    assert listed == {str(restaurant.owner_id)}
    assert (
        owner.request("PATCH", f"/api/staff/{victim_id}", json={"role": "owner"}).status_code == 404
    )
    assert owner.request("DELETE", f"/api/staff/{victim_id}").status_code == 404
    assert owner.request("POST", f"/api/staff/{victim_id}/login-link").status_code == 404
    assert owner.request("POST", f"/api/staff/{victim_id}/revoke-sessions").status_code == 404
    assert owner.request("POST", f"/api/staff/{victim_id}/pin-reset").status_code == 404

    # A's session secret presented under B's tenant id finds nothing.
    assert owner.cookie is not None
    forged = Device(client)
    forged.cookie = f"{other.tenant.value.hex}.{owner.cookie.split('.', 1)[1]}"
    assert forged.request("GET", "/api/me").status_code == 401
    assert forged.request("GET", "/api/staff").status_code == 401

    # B is unchanged.
    names = {s["id"]: s["role"] for s in other_owner.request("GET", "/api/staff").json()}
    assert names[str(victim_id)] == "kitchen"
