"""P4-T5 and P4-T6: pause, capacity and ordering settings.

Exit criteria covered here: pausing blocks new carts within 5 seconds; the capacity cap
rejects orders above the limit and lets them through again in the next slot. Also: each
setting is applied by the pricing P6 and P7 use (with placeholder tax values, B3), and
settings changes are audited and limited to owners and managers.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Device, Restaurant, audit_rows, join, publish_menu, run_async
from tests.api.conftest import services_of as services
from tests.db.conftest import PgDatabase
from velora.clock import FakeClock
from velora.ordering.gate import SLOT, slot_start
from velora.ordering.pricing import CartLine, Quote, QuoteRejected

# Placeholder tax rates for tests only. The real rates wait for the accountant (B3).
PLACEHOLDER_TAX = {"tax_cod_bps": 1600, "tax_transfer_bps": 500}


def patch(device: Device, **changes: Any) -> Any:
    return device.request("PATCH", "/api/settings/ordering", json=changes)


def gate_open(client: TestClient, restaurant: Restaurant) -> tuple[bool, str | None]:
    decision = run_async(client, services(client).ordering.check_new_cart, restaurant.tenant)
    return decision.open, decision.reason


def claim(client: TestClient, restaurant: Restaurant) -> bool:
    return run_async(client, services(client).ordering.claim_capacity, restaurant.tenant)


def quote(
    client: TestClient,
    restaurant: Restaurant,
    lines: list[CartLine],
    *,
    fulfilment: str = "delivery",
    payment_method: str = "cod",
) -> Quote:
    return run_async(
        client,
        services(client).ordering.quote,
        restaurant.tenant,
        lines,
        fulfilment=fulfilment,
        payment_method=payment_method,
    )


@pytest.fixture()
def live(owner: Device) -> Device:
    """A restaurant with a published menu and placeholder tax: ready to take carts."""
    publish_menu(owner)
    assert patch(owner, **PLACEHOLDER_TAX).status_code == 200
    return owner


# Pause (T5) -------------------------------------------------------------------------------


def test_pausing_blocks_new_carts_within_5_seconds(
    client: TestClient, live: Device, restaurant: Restaurant, clock: FakeClock
) -> None:
    assert gate_open(client, restaurant) == (True, None)
    started = time.monotonic()
    response = live.request("POST", "/api/ordering/pause", json={"minutes": 20, "reason": "rush"})
    assert response.status_code == 200
    assert gate_open(client, restaurant) == (False, "paused")
    assert time.monotonic() - started < 5
    assert response.json()["accepting_orders"] is False

    # The pause ends by itself...
    clock.advance(timedelta(minutes=20))
    assert gate_open(client, restaurant) == (True, None)
    # ...or when resumed.
    live.request("POST", "/api/ordering/pause", json={"minutes": 60})
    assert gate_open(client, restaurant)[0] is False
    assert live.request("POST", "/api/ordering/resume").json()["accepting_orders"] is True
    assert gate_open(client, restaurant) == (True, None)


def test_no_carts_without_a_published_menu(
    client: TestClient, owner: Device, restaurant: Restaurant
) -> None:
    assert gate_open(client, restaurant) == (False, "no_menu")


def test_kitchen_sees_the_status_but_cannot_pause(
    client: TestClient, live: Device, restaurant: Restaurant
) -> None:
    for role in ("kitchen", "kitchen_device"):
        device = join(client, live, role)
        assert device.request("GET", "/api/ordering/status").status_code == 200
        assert device.request("POST", "/api/ordering/pause", json={"minutes": 5}).status_code == 403
        assert device.request("POST", "/api/ordering/resume").status_code == 403
        assert patch(device, delivery_fee_paisa=0).status_code == 403
    assert gate_open(client, restaurant) == (True, None)


# Capacity (T5) ----------------------------------------------------------------------------


def test_capacity_cap_rejects_above_the_limit_and_reopens_next_slot(
    client: TestClient, live: Device, restaurant: Restaurant, clock: FakeClock
) -> None:
    assert patch(live, max_orders_per_slot=2).status_code == 200
    # Start mid-slot so the next claim falls in a new slot.
    clock.advance(slot_start(clock.now()) + SLOT - clock.now() - timedelta(minutes=5))

    assert claim(client, restaurant) is True
    assert claim(client, restaurant) is True
    assert claim(client, restaurant) is False
    assert gate_open(client, restaurant) == (False, "at_capacity")
    status = live.request("GET", "/api/ordering/status").json()
    assert (status["slot_orders"], status["max_orders_per_slot"]) == (2, 2)

    clock.advance(timedelta(minutes=5))
    assert gate_open(client, restaurant) == (True, None)
    assert claim(client, restaurant) is True
    assert claim(client, restaurant) is True
    assert claim(client, restaurant) is False


def test_concurrent_claims_never_overshoot_the_cap(
    client: TestClient, live: Device, restaurant: Restaurant
) -> None:
    import anyio

    assert patch(live, max_orders_per_slot=3).status_code == 200
    ordering = services(client).ordering

    async def burst() -> list[bool]:
        results: list[bool] = []

        async def one() -> None:
            results.append(await ordering.claim_capacity(restaurant.tenant))

        async with anyio.create_task_group() as tg:
            for _ in range(10):
                tg.start_soon(one)
        return results

    results = run_async(client, burst)
    assert sorted(results) == [False] * 7 + [True] * 3


def test_no_cap_means_no_limit(client: TestClient, live: Device, restaurant: Restaurant) -> None:
    assert all(claim(client, restaurant) for _ in range(5))


# Settings applied by pricing (T6) ---------------------------------------------------------

ZINGER_LARGE = CartLine("ZINGER", 2, (("Size", "Large"),))  # 2 x (650 + 150)
COKE = CartLine("COKE", 1)  # 120


def test_each_setting_is_applied_by_pricing(
    client: TestClient, live: Device, restaurant: Restaurant
) -> None:
    assert patch(live, delivery_fee_paisa=15000, delivery_min_order_paisa=80000).status_code == 200
    q = quote(client, restaurant, [ZINGER_LARGE, COKE], payment_method="cod")
    assert [line.line_total_paisa for line in q.lines] == [160000, 12000]
    assert q.subtotal_paisa == 172000
    assert q.delivery_fee_paisa == 15000
    assert (q.tax_bps, q.tax_paisa) == (1600, 27520)  # 16% of the item subtotal
    assert q.total_paisa == 172000 + 15000 + 27520

    # The tax line follows the payment method.
    q = quote(client, restaurant, [ZINGER_LARGE, COKE], payment_method="transfer")
    assert (q.tax_bps, q.tax_paisa) == (500, 8600)

    # Takeaway has no delivery fee and no delivery minimum.
    q = quote(client, restaurant, [COKE], fulfilment="takeaway")
    assert (q.delivery_fee_paisa, q.subtotal_paisa) == (0, 12000)

    # Delivery below the minimum is refused.
    with pytest.raises(QuoteRejected) as below:
        quote(client, restaurant, [COKE])
    assert below.value.code == "below_minimum"

    # Switching options off is applied too.
    patch(live, delivery_enabled=False, transfer_enabled=False)
    with pytest.raises(QuoteRejected) as off:
        quote(client, restaurant, [ZINGER_LARGE])
    assert off.value.code == "fulfilment_unavailable"
    with pytest.raises(QuoteRejected) as no_transfer:
        quote(client, restaurant, [ZINGER_LARGE], fulfilment="takeaway", payment_method="transfer")
    assert no_transfer.value.code == "payment_unavailable"


def test_an_unset_tax_rate_refuses_the_quote(
    client: TestClient, live: Device, restaurant: Restaurant
) -> None:
    patch(live, tax_transfer_bps=None)
    with pytest.raises(QuoteRejected) as rejected:
        quote(client, restaurant, [ZINGER_LARGE], payment_method="transfer")
    assert rejected.value.code == "tax_not_configured"
    assert quote(client, restaurant, [ZINGER_LARGE], payment_method="cod").tax_bps == 1600


def test_settings_changes_are_audited_with_old_and_new_values(
    live: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    response = patch(live, delivery_fee_paisa=15000, cod_first_order_limit_paisa=300000)
    assert response.status_code == 200
    assert response.json()["cod_first_order_limit_paisa"] == 300000
    with db.admin() as conn:
        rows = [
            r for r in audit_rows(conn, restaurant.tenant) if r[0] == "settings.ordering_updated"
        ]
    changes = [r[4]["changes"] for r in rows]
    assert {
        "delivery_fee_paisa": {"from": 0, "to": 15000},
        "cod_first_order_limit_paisa": {"from": None, "to": 300000},
    } in changes
    assert all(r[1:3] == ("staff", live.staff_id) for r in rows)


@pytest.mark.parametrize(
    "changes",
    [
        {"delivery_fee_paisa": -1},
        {"delivery_fee_paisa": None},
        {"tax_cod_bps": 5001},
        {"delivery_enabled": False, "takeaway_enabled": False},
        {"cod_enabled": False, "transfer_enabled": False},
        {"pickup_min_lead_minutes": 60, "pickup_max_ahead_minutes": 30},
        {"max_orders_per_slot": 0},
        {"unknown_setting": 1},
    ],
)
def test_invalid_settings_are_refused(live: Device, changes: dict[str, Any]) -> None:
    assert patch(live, **changes).status_code == 422


def test_a_manager_can_change_settings(client: TestClient, live: Device) -> None:
    manager = join(client, live, "manager")
    assert patch(manager, delivery_fee_paisa=10000).status_code == 200
    assert manager.request("GET", "/api/settings/ordering").json()["delivery_fee_paisa"] == 10000
