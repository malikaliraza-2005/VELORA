"""P7-T3: the staff order endpoints (the dashboard screens are P9).

- Viewing needs `ORDERS_VIEW`, moving `ORDERS_UPDATE`: every role, including the shared
  kitchen device, can accept and progress orders.
- A move names the status the staff member saw; a stale or repeated move is refused (409).
- Payment verification is not a generic move (P8), a refusal needs a reason, and another
  restaurant's order is not found.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import (
    Device,
    Restaurant,
    connect_channel,
    join,
    new_restaurant,
)
from tests.db.conftest import PgDatabase
from tests.db.seed import insert_order
from velora.admin.onboarding import Onboarding
from velora.config import Settings


def _order(db: PgDatabase, restaurant: Restaurant, **kwargs: str) -> uuid.UUID:
    with db.admin() as conn:
        return insert_order(conn, restaurant.tenant.value, **kwargs)


def _move(device: Device, order: uuid.UUID, body: dict[str, str]) -> tuple[int, Any]:
    response = device.request("POST", f"/api/orders/{order}/status", json=body)
    return response.status_code, response.json()


def test_the_kitchen_device_sees_and_progresses_orders(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    order = _order(db, restaurant, fulfilment="takeaway")
    tablet = join(client, owner, "kitchen_device")
    listed = tablet.request("GET", "/api/orders").json()
    assert [o["id"] for o in listed] == [str(order)]
    assert listed[0]["next_statuses"] == ["accepted", "rejected", "cancelled"]
    for start, to in (
        ("cod_confirmed", "accepted"),
        ("accepted", "preparing"),
        ("preparing", "ready"),
        ("ready", "completed"),
    ):
        status, body = _move(tablet, order, {"from": start, "to": to})
        assert status == 200, body
        assert body["status"] == to
    detail = tablet.request("GET", f"/api/orders/{order}").json()
    assert [(m["from_status"], m["to_status"]) for m in detail["history"]] == [
        ("cod_confirmed", "accepted"),
        ("accepted", "preparing"),
        ("preparing", "ready"),
        ("ready", "completed"),
    ]
    assert detail["next_statuses"] == []
    # A completed order has ended: it leaves the active list.
    assert tablet.request("GET", "/api/orders").json() == []
    assert len(tablet.request("GET", "/api/orders?active=false").json()) == 1


def test_a_stale_or_repeated_move_is_refused(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    order = _order(db, restaurant)
    move = {"from": "cod_confirmed", "to": "accepted"}
    assert _move(owner, order, move)[0] == 200
    # The same tap again, or another device that still shows the old status.
    status, body = _move(owner, order, move)
    assert status == 409 and "now accepted" in body["detail"]
    status, _ = _move(owner, order, {"from": "cod_confirmed", "to": "rejected", "reason": "x"})
    assert status == 409


def test_illegal_moves_and_payment_verification_are_refused(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    cod = _order(db, restaurant)
    transfer = _order(db, restaurant, status="awaiting_transfer", payment_method="transfer")
    assert _move(owner, cod, {"from": "cod_confirmed", "to": "completed"})[0] == 409
    assert _move(owner, transfer, {"from": "awaiting_transfer", "to": "accepted"})[0] == 409
    # Only the system expires an order.
    assert _move(owner, transfer, {"from": "awaiting_transfer", "to": "expired"})[0] == 409
    # P8: verified with the transaction reference and PIN, never by a plain move.
    status, body = _move(
        owner, transfer, {"from": "awaiting_transfer", "to": "paid_staff_verified"}
    )
    assert status == 403 and "reference and a PIN" in body["detail"]
    # An unknown status is a validation error.
    assert _move(owner, cod, {"from": "cod_confirmed", "to": "eaten"})[0] == 422


def test_refusing_an_order_needs_a_reason(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    order = _order(db, restaurant)
    status, body = _move(owner, order, {"from": "cod_confirmed", "to": "rejected"})
    assert status == 422 and "say why" in body["detail"]
    status, body = _move(
        owner, order, {"from": "cod_confirmed", "to": "rejected", "reason": "kitchen closed"}
    )
    assert status == 200
    assert body["history"][-1]["reason"] == "kitchen closed"


def test_another_restaurants_order_is_not_found(
    owner: Device,
    db: PgDatabase,
    platform: Onboarding,
    settings: Settings,
) -> None:
    other = new_restaurant(platform, settings)
    connect_channel(db, platform, other.tenant)
    theirs = _order(db, other)
    assert owner.request("GET", f"/api/orders/{theirs}").status_code == 404
    assert _move(owner, theirs, {"from": "cod_confirmed", "to": "accepted"})[0] == 404
    with db.admin() as conn:
        row = conn.execute("SELECT status FROM velora.orders WHERE id = %s", (theirs,)).fetchone()
    assert row == ("cod_confirmed",)
