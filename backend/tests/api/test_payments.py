"""P8-T1, T2, T4, T5, T6: the staff payment endpoints.

Exit criteria covered here:
- **a confirmation without a PIN, or from another tenant, is rejected;**
- **a duplicate reference is flagged** (refused, audited, and the other order named);
- the late-payment review offers only "accept" or "refund";
- the reconciliation report lists every staff-verified transfer of the day, and only the
  owner sees it or reverses a payment.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from fastapi.testclient import TestClient

from tests.api.conftest import (
    Device,
    Restaurant,
    audit_rows,
    connect_channel,
    join,
    new_restaurant,
    run_async,
    services_of,
)
from tests.api.test_roles_and_pins import GOOD_PIN, OTHER_PIN, set_pin
from tests.db.conftest import PgDatabase
from tests.db.seed import insert_order
from velora.admin.onboarding import Onboarding
from velora.clock import FakeClock
from velora.config import Settings
from velora.payments.service import DuplicateReferenceError


def _order(db: PgDatabase, restaurant: Restaurant, status: str = "awaiting_transfer") -> uuid.UUID:
    with db.admin() as conn:
        return insert_order(conn, restaurant.tenant.value, status=status, payment_method="transfer")


def _verify(
    device: Device,
    order: uuid.UUID,
    reference: str = "FT-2026-0001",
    pin: str | None = GOOD_PIN,
    **extra: Any,
) -> httpx2.Response:
    body: dict[str, Any] = {"reference": reference, **extra}
    if pin is not None:
        body["pin"] = pin
    return device.request("POST", f"/api/orders/{order}/payment/verify", json=body)


def _status(db: PgDatabase, order: uuid.UUID) -> str:
    with db.admin() as conn:
        row = conn.execute("SELECT status FROM velora.orders WHERE id = %s", (order,)).fetchone()
    assert row is not None
    return str(row[0])


def _ledger(db: PgDatabase, order: uuid.UUID) -> list[tuple[Any, ...]]:
    with db.admin() as conn:
        return conn.execute(
            "SELECT status, reference, reference_key, verified_by_staff_id, "
            "recorded_by_staff_id, late, amount_paisa FROM velora.payments WHERE order_id = %s "
            "ORDER BY created_at, status DESC",
            (order,),
        ).fetchall()


def _actions(db: PgDatabase, restaurant: Restaurant, action: str) -> list[dict[str, Any]]:
    with db.admin() as conn:
        return [r[4] for r in audit_rows(conn, restaurant.tenant) if r[0] == action]


# Verification (T4) -------------------------------------------------------------------------


def test_staff_verify_a_transfer_with_the_reference_and_their_pin(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    order = _order(db, restaurant)
    before = owner.request("GET", f"/api/orders/{order}/payment").json()
    assert (before["actions"], before["ledger"]) == (["verify"], [])

    response = _verify(owner, order, reference=" ft 2026-0001 ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "paid_staff_verified"
    assert body["actions"] == []
    (entry,) = body["ledger"]
    assert (entry["status"], entry["reference"], entry["amount_paisa"]) == (
        "verified",
        "ft 2026-0001",
        90000,
    )
    assert _ledger(db, order) == [
        ("verified", "ft 2026-0001", "FT20260001", owner.staff_id, owner.staff_id, False, 90000)
    ]
    # Audited, and the history names the confirmer.
    (verified,) = _actions(db, restaurant, "payment.verified")
    assert verified["reference"] == "ft 2026-0001"
    assert verified["confirmer_id"] == str(owner.staff_id)
    with db.admin() as conn:
        assert conn.execute(
            "SELECT from_status, actor_kind, actor_staff_id FROM velora.order_transitions "
            "WHERE order_id = %s",
            (order,),
        ).fetchall() == [("awaiting_transfer", "staff", owner.staff_id)]
    # Verifying again is refused: the order is no longer awaiting a transfer.
    assert _verify(owner, order, reference="FT-2026-0002").status_code == 409


def test_a_confirmation_without_a_pin_is_rejected(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    order = _order(db, restaurant)
    assert _verify(owner, order, pin=None).status_code == 422
    assert _verify(owner, order, pin="").status_code == 422
    assert _verify(owner, order, pin=OTHER_PIN).status_code == 403
    assert _status(db, order) == "awaiting_transfer"
    assert _ledger(db, order) == []
    assert [d["reason"] for d in _actions(db, restaurant, "payment.pin_rejected")] == ["wrong_pin"]


def test_a_staff_member_without_a_pin_cannot_confirm(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    manager = join(client, owner, "manager")  # no PIN set
    order = _order(db, restaurant)
    assert _verify(manager, order).status_code == 403
    assert _status(db, order) == "awaiting_transfer"


def test_a_confirmation_from_another_tenant_is_rejected(
    client: TestClient,
    owner: Device,
    restaurant: Restaurant,
    db: PgDatabase,
    platform: Onboarding,
    settings: Settings,
) -> None:
    order = _order(db, restaurant)
    other = new_restaurant(platform, settings)
    connect_channel(db, platform, other.tenant)
    intruder = Device(client)
    assert intruder.login(other.owner_link).status_code == 200
    assert set_pin(intruder, GOOD_PIN) == 204
    assert _verify(intruder, order).status_code == 404
    assert intruder.request("GET", f"/api/orders/{order}/payment").status_code == 404
    # Naming another restaurant's staff member as the confirmer doesn't work either.
    tablet = join(client, owner, "kitchen_device")
    assert _verify(tablet, order, confirmer_id=str(intruder.staff_id)).status_code == 403
    assert _status(db, order) == "awaiting_transfer"
    assert _ledger(db, order) == []


def test_a_kitchen_device_confirms_with_a_named_persons_pin(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    cook = join(client, owner, "kitchen")
    assert set_pin(cook, GOOD_PIN) == 204
    tablet = join(client, owner, "kitchen_device")
    order = _order(db, restaurant)
    assert _verify(tablet, order).status_code == 422  # who is confirming?
    response = _verify(tablet, order, confirmer_id=str(cook.staff_id))
    assert response.status_code == 200, response.text
    ((_, _, _, confirmer, recorder, _, _),) = _ledger(db, order)
    assert (confirmer, recorder) == (cook.staff_id, tablet.staff_id)


def test_cash_orders_and_generic_moves_cannot_be_marked_paid(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    with db.admin() as conn:
        cash = insert_order(conn, restaurant.tenant.value)
    assert _verify(owner, cash).status_code == 409
    transfer = _order(db, restaurant)
    move = {"from": "awaiting_transfer", "to": "paid_staff_verified"}
    assert owner.request("POST", f"/api/orders/{transfer}/status", json=move).status_code == 403
    assert _status(db, transfer) == "awaiting_transfer"


def test_a_reference_needs_four_letters_or_digits(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    order = _order(db, restaurant)
    assert _verify(owner, order, reference="--1--").status_code == 422
    assert _verify(owner, order, reference="x" * 65).status_code == 422
    # Refused before the PIN is checked, so no attempt is used up.
    assert _actions(db, restaurant, "payment.pin_rejected") == []


# Duplicate references (T4) ---------------------------------------------------------------


def test_a_duplicate_reference_is_flagged(
    client: TestClient,
    owner: Device,
    restaurant: Restaurant,
    db: PgDatabase,
    platform: Onboarding,
    settings: Settings,
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    first, second = _order(db, restaurant), _order(db, restaurant)
    assert _verify(owner, first, reference="TXN 77881").status_code == 200
    first_ref = owner.request("GET", f"/api/orders/{first}/payment").json()["reference"]

    response = _verify(owner, second, reference="txn-77881")  # same key, typed differently
    assert response.status_code == 409
    assert f"order #{first_ref}" in response.json()["detail"]
    assert _status(db, second) == "awaiting_transfer"
    assert _ledger(db, second) == []
    (flag,) = _actions(db, restaurant, "payment.duplicate_reference")
    assert (flag["reference"], flag["already_on"]) == ("txn-77881", first_ref)

    # Unique per tenant: another restaurant may see the same reference.
    other = new_restaurant(platform, settings)
    connect_channel(db, platform, other.tenant)
    other_owner = Device(client)
    assert other_owner.login(other.owner_link).status_code == 200
    assert set_pin(other_owner, GOOD_PIN) == 204
    with db.admin() as conn:
        theirs = insert_order(
            conn, other.tenant.value, status="awaiting_transfer", payment_method="transfer"
        )
    assert _verify(other_owner, theirs, reference="TXN77881").status_code == 200


def test_two_simultaneous_confirmations_with_one_reference_verify_one_order(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    first, second = _order(db, restaurant), _order(db, restaurant)
    services = services_of(client)
    assert owner.cookie is not None
    session = run_async(client, services.sessions.authenticate, owner.cookie)
    assert session is not None

    async def both() -> list[Any]:
        return await asyncio.gather(
            *(
                services.payments.verify(session, order, reference="RACE-1234", pin=GOOD_PIN)
                for order in (first, second)
            ),
            return_exceptions=True,
        )

    results = run_async(client, both)
    assert sorted(type(r).__name__ for r in results) == ["DuplicateReferenceError", "PaymentView"]
    assert sum(isinstance(r, DuplicateReferenceError) for r in results) == 1
    assert sorted([_status(db, first), _status(db, second)]) == [
        "awaiting_transfer",
        "paid_staff_verified",
    ]


# Late payments (T5) ----------------------------------------------------------------------


def _late(device: Device, order: uuid.UUID, **body: Any) -> httpx2.Response:
    payload = {"reference": "LATE-0001", "pin": GOOD_PIN, **body}
    return device.request("POST", f"/api/orders/{order}/payment/late-review", json=payload)


def test_a_late_payment_offers_only_accept_or_refund(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    order = _order(db, restaurant, status="expired")
    view = owner.request("GET", f"/api/orders/{order}/payment").json()
    assert view["actions"] == ["late_review"]
    # The ordinary verification and the generic moves don't apply to an expired order.
    assert _verify(owner, order).status_code == 409
    for to in ("paid_staff_verified", "refund_pending", "accepted"):
        move = {"from": "expired", "to": to}
        assert owner.request("POST", f"/api/orders/{order}/status", json=move).status_code in (
            403,
            409,
        )
    # Only the two decisions exist; refunding needs a reason.
    assert _late(owner, order, decision="ignore").status_code == 422
    assert _late(owner, order, decision="paid").status_code == 422
    assert _late(owner, order, decision="refund").status_code == 422
    assert _status(db, order) == "expired"
    assert _ledger(db, order) == []


def test_a_late_payment_accepted_is_verified_then_accepted(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    order = _order(db, restaurant, status="expired")
    response = _late(owner, order, decision="accept")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    ((status, _, _, _, _, late, _),) = _ledger(db, order)
    assert (status, late) == ("verified", True)
    with db.admin() as conn:
        assert conn.execute(
            "SELECT from_status, to_status FROM velora.order_transitions WHERE order_id = %s "
            "ORDER BY seq",
            (order,),
        ).fetchall() == [("expired", "paid_staff_verified"), ("paid_staff_verified", "accepted")]


def test_a_late_payment_refunded_is_recorded_and_refund_pending(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    order = _order(db, restaurant, status="expired")
    response = _late(owner, order, decision="refund", reason="kitchen already closed")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "refund_pending"
    ((status, _, _, _, _, late, _),) = _ledger(db, order)
    assert (status, late) == ("verified", True)
    # The refund itself is made from the restaurant's own account; staff then close it.
    move = {"from": "refund_pending", "to": "cancelled", "reason": "refunded via Meezan app"}
    assert owner.request("POST", f"/api/orders/{order}/status", json=move).status_code == 200


# Reconciliation and reversal (T6) --------------------------------------------------------


def test_the_reconciliation_report_lists_every_verified_transfer_of_the_day(
    client: TestClient,
    owner: Device,
    restaurant: Restaurant,
    db: PgDatabase,
    clock: FakeClock,
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    cook = join(client, owner, "kitchen")
    assert set_pin(cook, GOOD_PIN) == 204
    orders = [_order(db, restaurant) for _ in range(3)]
    assert _verify(owner, orders[0], reference="DAY-0001").status_code == 200
    clock.advance(timedelta(minutes=5))
    assert _verify(cook, orders[1], reference="DAY-0002").status_code == 200
    clock.advance(timedelta(minutes=5))
    late = _order(db, restaurant, status="expired")
    assert _late(owner, late, decision="accept", reference="DAY-0003").status_code == 200
    # orders[2] is never paid, so it is not listed.

    report = owner.request("GET", "/api/payments/reconciliation?day=2026-10-21").json()
    assert [line["reference"] for line in report["lines"]] == ["DAY-0001", "DAY-0002", "DAY-0003"]
    assert [line["verified_by_staff_id"] for line in report["lines"]] == [
        str(owner.staff_id),
        str(cook.staff_id),
        str(owner.staff_id),
    ]
    assert [line["late"] for line in report["lines"]] == [False, False, True]
    assert (report["verified_count"], report["verified_paisa"], report["net_paisa"]) == (
        3,
        270000,
        270000,
    )
    # Today (Pakistan time) by default; another day is empty.
    assert owner.request("GET", "/api/payments/reconciliation").json()["verified_count"] == 3
    other_day = owner.request("GET", "/api/payments/reconciliation?day=2026-10-22").json()
    assert other_day["lines"] == []
    assert clock.now().date() == datetime(2026, 10, 21, tzinfo=UTC).date()


def test_only_the_owner_sees_the_report_and_reverses(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    manager = join(client, owner, "manager")
    assert set_pin(manager, GOOD_PIN) == 204
    order = _order(db, restaurant)
    assert _verify(manager, order).status_code == 200
    (payment,) = owner.request("GET", f"/api/orders/{order}/payment").json()["ledger"]
    for role in ("manager", "kitchen", "kitchen_device"):
        device = manager if role == "manager" else join(client, owner, role)
        assert device.request("GET", "/api/payments/reconciliation").status_code == 403
        reverse = {"pin": GOOD_PIN, "reason": "not in the account"}
        response = device.request("POST", f"/api/payments/{payment['id']}/reverse", json=reverse)
        assert response.status_code == 403
    assert _status(db, order) == "paid_staff_verified"
    assert len(_ledger(db, order)) == 1


def test_the_owner_reverses_a_verification_and_it_is_audited(
    owner: Device, restaurant: Restaurant, db: PgDatabase, clock: FakeClock
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    order = _order(db, restaurant)
    assert _verify(owner, order, reference="REV-0001").status_code == 200
    (payment,) = owner.request("GET", f"/api/orders/{order}/payment").json()["ledger"]
    path = f"/api/payments/{payment['id']}/reverse"

    wrong = owner.request("POST", path, json={"pin": OTHER_PIN, "reason": "typo"})
    assert wrong.status_code == 403
    response = owner.request("POST", path, json={"pin": GOOD_PIN, "reason": "wrong order"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "awaiting_transfer"
    assert [(e["status"], e["corrects_payment_id"]) for e in body["ledger"]] == [
        ("verified", None),
        ("reversed", payment["id"]),
    ]
    (reversed_,) = _actions(db, restaurant, "payment.reversed")
    assert (reversed_["payment_id"], reversed_["reason"]) == (payment["id"], "wrong order")
    # A reversal is final and happens once; the ledger rows themselves never change.
    again = owner.request("POST", path, json={"pin": GOOD_PIN, "reason": "again"})
    assert again.status_code == 409
    # The reference is free again, and the report shows the reversal.
    clock.advance(timedelta(minutes=5))
    assert _verify(owner, order, reference="REV-0001").status_code == 200
    report = owner.request("GET", "/api/payments/reconciliation").json()
    assert [line["reversed_at"] is not None for line in report["lines"]] == [True, False]
    assert (report["verified_paisa"], report["reversed_paisa"], report["net_paisa"]) == (
        180000,
        90000,
        90000,
    )


def test_reversing_after_acceptance_keeps_the_order_status(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    assert set_pin(owner, GOOD_PIN) == 204
    order = _order(db, restaurant)
    assert _verify(owner, order).status_code == 200
    move = {"from": "paid_staff_verified", "to": "accepted"}
    assert owner.request("POST", f"/api/orders/{order}/status", json=move).status_code == 200
    (payment,) = owner.request("GET", f"/api/orders/{order}/payment").json()["ledger"]
    response = owner.request(
        "POST",
        f"/api/payments/{payment['id']}/reverse",
        json={"pin": GOOD_PIN, "reason": "bounced"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


# COD blocklist (T2) ------------------------------------------------------------------------


def test_managers_block_and_unblock_cod_and_it_is_audited(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    with db.admin() as conn:
        order = insert_order(conn, restaurant.tenant.value)
    customer = owner.request("GET", f"/api/orders/{order}/payment").json()["customer_id"]
    path = f"/api/customers/{customer}/cod-block"
    cook = join(client, owner, "kitchen")
    assert cook.request("PUT", path, json={"reason": "fake order"}).status_code == 403
    manager = join(client, owner, "manager")
    assert manager.request("PUT", path, json={"reason": "fake order"}).status_code == 204
    assert owner.request("GET", f"/api/orders/{order}/payment").json()["cod_blocked"] is True
    assert manager.request("DELETE", path).status_code == 204
    assert owner.request("GET", f"/api/orders/{order}/payment").json()["cod_blocked"] is False
    assert [d["reason"] for d in _actions(db, restaurant, "customer.cod_blocked")] == ["fake order"]
    assert len(_actions(db, restaurant, "customer.cod_unblocked")) == 1
    unknown = f"/api/customers/{uuid.uuid4()}/cod-block"
    assert manager.request("PUT", unknown, json={"reason": "x"}).status_code == 404
