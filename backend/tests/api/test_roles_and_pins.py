"""P3-T4 roles and P3-T5 payment PINs.

Exit criteria covered here: a kitchen-device session can't change the menu or settings;
confirming a payment without a valid PIN is rejected and logged.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tests.api.conftest import Device, Restaurant, audit_rows, invite, join
from tests.db.conftest import PgDatabase
from velora.api.deps import require
from velora.clock import FakeClock
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.identity.staff import PIN_LOCKOUT, PIN_MAX_FAILURES

GOOD_PIN = "480135"
OTHER_PIN = "731902"


@pytest.fixture()
def app(app: FastAPI) -> FastAPI:
    """Adds a stand-in menu endpoint guarded the way P4's menu endpoints must be."""

    @app.put("/test/menu")
    async def edit_menu(
        session: Annotated[StaffSession, Depends(require(Permission.MENU_EDIT))],
    ) -> dict[str, str]:
        return {"ok": "menu changed"}

    return app


def pin_rejections(db: PgDatabase, restaurant: Restaurant) -> list[tuple[str, uuid.UUID | None]]:
    with db.admin() as conn:
        return [
            (r[4]["reason"], r[3])
            for r in audit_rows(conn, restaurant.tenant)
            if r[0] == "payment.pin_rejected"
        ]


def set_pin(device: Device, pin: str, current: str | None = None) -> int:
    body = {"new_pin": pin, "current_pin": current}
    return device.request("PUT", "/api/me/pin", json=body).status_code


def confirm(device: Device, pin: str, confirmer: uuid.UUID | None = None) -> int:
    body = {"pin": pin, "confirmer_id": None if confirmer is None else str(confirmer)}
    return device.request("POST", "/test/payments/confirm", json=body).status_code


# Roles (T4) -------------------------------------------------------------------------------


def test_kitchen_device_cannot_change_the_menu_or_settings(
    client: TestClient, owner: Device
) -> None:
    device = join(client, owner, "kitchen_device")
    assert device.request("PUT", "/test/menu").status_code == 403
    response = device.request("PATCH", "/api/settings", json={"display_name": "Hacked"})
    assert response.status_code == 403
    assert device.request("GET", "/api/staff").status_code == 403
    assert (
        device.request(
            "POST", "/api/staff", json={"display_name": "x", "role": "owner"}
        ).status_code
        == 403
    )
    # The owner and a manager can.
    assert owner.request("PUT", "/test/menu").status_code == 200
    manager = join(client, owner, "manager")
    assert (
        manager.request("PATCH", "/api/settings", json={"display_name": "New"}).status_code == 204
    )


def test_only_the_owner_invites_or_changes_staff(client: TestClient, owner: Device) -> None:
    manager = join(client, owner, "manager")
    assert manager.request("GET", "/api/staff").status_code == 200
    response = manager.request("POST", "/api/staff", json={"display_name": "x", "role": "kitchen"})
    assert response.status_code == 403
    response = manager.request("PATCH", f"/api/staff/{owner.staff_id}", json={"role": "kitchen"})
    assert response.status_code == 403


def test_the_owner_cannot_demote_or_remove_themselves(owner: Device) -> None:
    response = owner.request("PATCH", f"/api/staff/{owner.staff_id}", json={"role": "manager"})
    assert response.status_code == 409
    assert owner.request("DELETE", f"/api/staff/{owner.staff_id}").status_code == 409


def test_invalid_role_is_rejected(owner: Device) -> None:
    response = owner.request("POST", "/api/staff", json={"display_name": "x", "role": "admin"})
    assert response.status_code == 422


# PIN setting (T5) -------------------------------------------------------------------------


def test_setting_a_pin_stores_only_a_hash(owner: Device, db: PgDatabase) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    with db.admin() as conn:
        row = conn.execute(
            "SELECT pin_hash FROM velora.staff_members WHERE id = %s", (owner.staff_id,)
        ).fetchone()
    assert row is not None and row[0].startswith("scrypt$") and GOOD_PIN not in row[0]
    staff = {s["id"]: s for s in owner.request("GET", "/api/staff").json()}
    assert staff[str(owner.staff_id)]["has_pin"] is True


def test_weak_pins_are_refused(owner: Device) -> None:
    for pin in ["1234", "111111", "123456", "abcdef"]:
        assert set_pin(owner, pin) == 422


def test_changing_a_pin_needs_the_current_one(owner: Device) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    assert set_pin(owner, OTHER_PIN) == 403
    assert set_pin(owner, OTHER_PIN, current="000001") == 403
    assert set_pin(owner, OTHER_PIN, current=GOOD_PIN) == 204
    assert confirm(owner, OTHER_PIN) == 200


def test_a_kitchen_device_cannot_hold_a_pin(client: TestClient, owner: Device) -> None:
    device = join(client, owner, "kitchen_device")
    assert set_pin(device, GOOD_PIN) == 403


# Payment PIN gate (T5) --------------------------------------------------------------------


def test_confirming_a_payment_without_a_valid_pin_is_rejected_and_logged(
    client: TestClient, owner: Device, db: PgDatabase, restaurant: Restaurant
) -> None:
    manager = join(client, owner, "manager")
    assert confirm(manager, GOOD_PIN) == 403  # no PIN set yet
    assert set_pin(manager, GOOD_PIN) == 204
    assert confirm(manager, OTHER_PIN) == 403  # wrong PIN
    assert confirm(manager, "") == 403  # no PIN
    assert confirm(manager, "12") == 403  # malformed PIN
    assert sorted(pin_rejections(db, restaurant)) == sorted(
        [("no_pin_set", manager.staff_id)] + [("wrong_pin", manager.staff_id)] * 3
    )
    response = manager.request("POST", "/test/payments/confirm", json={"pin": GOOD_PIN})
    assert response.json() == {"confirmed_by": str(manager.staff_id)}


def test_a_person_confirms_only_with_their_own_pin(client: TestClient, owner: Device) -> None:
    manager = join(client, owner, "manager")
    assert set_pin(owner, GOOD_PIN) == 204
    assert confirm(manager, GOOD_PIN, confirmer=owner.staff_id) == 403


def test_on_a_kitchen_device_a_named_person_confirms_with_their_pin(
    client: TestClient, owner: Device, db: PgDatabase, restaurant: Restaurant
) -> None:
    cook = join(client, owner, "kitchen")
    assert set_pin(cook, GOOD_PIN) == 204
    tablet = join(client, owner, "kitchen_device")

    assert confirm(tablet, GOOD_PIN) == 422  # must say who is confirming
    assert confirm(tablet, OTHER_PIN, confirmer=cook.staff_id) == 403
    response = tablet.request(
        "POST", "/test/payments/confirm", json={"pin": GOOD_PIN, "confirmer_id": str(cook.staff_id)}
    )
    assert response.json() == {"confirmed_by": str(cook.staff_id)}
    # The tablet itself can never be the confirmer.
    assert confirm(tablet, GOOD_PIN, confirmer=tablet.staff_id) == 403
    assert ("not_permitted", tablet.staff_id) in pin_rejections(db, restaurant)


def test_repeated_wrong_pins_lock_the_pin(
    client: TestClient, owner: Device, clock: FakeClock, db: PgDatabase, restaurant: Restaurant
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    for _ in range(PIN_MAX_FAILURES):
        assert confirm(owner, OTHER_PIN) == 403
    assert confirm(owner, GOOD_PIN) == 403  # locked, even with the right PIN
    reasons = [r for r, _ in pin_rejections(db, restaurant)]
    assert reasons.count("wrong_pin_now_locked") == 1
    assert "locked" in reasons

    clock.advance(PIN_LOCKOUT + timedelta(seconds=1))
    assert confirm(owner, GOOD_PIN) == 200


def test_the_owner_can_reset_a_forgotten_pin(client: TestClient, owner: Device) -> None:
    cook = join(client, owner, "kitchen")
    assert set_pin(cook, GOOD_PIN) == 204
    assert owner.request("POST", f"/api/staff/{cook.staff_id}/pin-reset").status_code == 204
    assert confirm(cook, GOOD_PIN) == 403
    assert set_pin(cook, OTHER_PIN) == 204  # no current PIN needed after a reset
    assert confirm(cook, OTHER_PIN) == 200


def test_a_removed_persons_pin_no_longer_confirms(client: TestClient, owner: Device) -> None:
    cook_id, link = invite(owner, "kitchen")
    cook = Device(client)
    assert cook.login(link).status_code == 200
    assert set_pin(cook, GOOD_PIN) == 204
    tablet = join(client, owner, "kitchen_device")
    assert owner.request("DELETE", f"/api/staff/{cook_id}").status_code == 204
    assert confirm(tablet, GOOD_PIN, confirmer=cook_id) == 403
