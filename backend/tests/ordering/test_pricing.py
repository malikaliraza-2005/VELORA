"""P4-T6 pricing and settings rules, without a database. Prices always come from the menu."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from velora.identity.errors import InvalidInputError
from velora.menu.model import Category, Item, Menu, ModifierGroup, Option
from velora.menu.store import LiveMenu, Version
from velora.ordering.gate import slot_start
from velora.ordering.pricing import (
    CartLine,
    QuoteRejected,
    check_pickup_time,
    cod_allowed,
    price_cart,
    tax_for,
    transfer_deadline,
)
from velora.ordering.settings import OrderingSettings, apply_changes

NOW = datetime(2026, 10, 21, 9, 7, tzinfo=UTC)

MENU = Menu(
    (
        Category(
            "Burgers",
            (
                Item(
                    "ZINGER",
                    "Zinger Burger",
                    65000,
                    groups=(
                        ModifierGroup("Size", 1, 1, (Option("Regular", 0), Option("Large", 15000))),
                        ModifierGroup(
                            "Extras", 0, 2, (Option("Cheese", 5000), Option("Jalapeno", 3000))
                        ),
                    ),
                ),
            ),
        ),
        Category("Drinks", (Item("COKE", "Coca-Cola", 12000),)),
    )
)
LIVE = LiveMenu(Version(uuid.uuid4(), 1, 1, NOW, NOW), MENU, frozenset())
SETTINGS = OrderingSettings(
    delivery_enabled=True,
    takeaway_enabled=True,
    delivery_fee_paisa=15000,
    delivery_min_order_paisa=0,
    pickup_min_lead_minutes=20,
    pickup_max_ahead_minutes=240,
    tax_cod_bps=1600,
    tax_transfer_bps=500,
    cod_enabled=True,
    transfer_enabled=True,
    cod_first_order_limit_paisa=300000,
    transfer_window_minutes=30,
    max_orders_per_slot=None,
    paused_until=None,
    pause_reason=None,
)
REGULAR = (("Size", "Regular"),)


def rejected(lines: list[CartLine], live: LiveMenu = LIVE, **kwargs: object) -> str:
    options: dict[str, object] = {"fulfilment": "delivery", "payment_method": "cod", **kwargs}
    settings = options.pop("settings", SETTINGS)
    assert isinstance(settings, OrderingSettings)
    with pytest.raises(QuoteRejected) as failed:
        price_cart(live, settings, lines, **options)  # type: ignore[arg-type]
    return failed.value.code


def test_unit_price_is_item_plus_chosen_options() -> None:
    line = CartLine("zinger", 3, (("size", "LARGE"), ("Extras", "Cheese"), ("Extras", "Jalapeno")))
    quote = price_cart(LIVE, SETTINGS, [line], fulfilment="takeaway", payment_method="cod")
    (quoted,) = quote.lines
    assert quoted.unit_price_paisa == 65000 + 15000 + 5000 + 3000
    assert quoted.line_total_paisa == quoted.unit_price_paisa * 3
    assert quote.total_paisa == quote.subtotal_paisa + quote.tax_paisa
    assert quote.menu_version_id == LIVE.version.id


def test_tax_rounds_half_up_to_the_paisa() -> None:
    assert tax_for(100, 1650) == 17  # 16.5 -> 17
    assert tax_for(100, 1640) == 16  # 16.4 -> 16
    assert tax_for(0, 1600) == 0


@pytest.mark.parametrize(
    ("lines", "code"),
    [
        ([], "empty_cart"),
        ([CartLine("BURGER", 1)], "unknown_item"),
        ([CartLine("ZINGER", 1)], "choice_required"),
        ([CartLine("ZINGER", 1, (("Size", "Huge"),))], "invalid_choice"),
        ([CartLine("ZINGER", 1, (("Colour", "Red"),))], "invalid_choice"),
        ([CartLine("ZINGER", 1, (("Size", "Regular"), ("Size", "Large")))], "invalid_choice"),
        (
            [CartLine("ZINGER", 1, (*REGULAR, ("Extras", "Cheese"), ("Extras", "Cheese")))],
            "invalid_choice",
        ),
        ([CartLine("COKE", 0)], "quantity"),
        ([CartLine("COKE", 51)], "quantity"),
        ([CartLine("COKE", 1)] * 31, "too_many_lines"),
    ],
)
def test_invalid_carts_are_refused(lines: list[CartLine], code: str) -> None:
    assert rejected(lines) == code


def test_unavailable_items_are_refused() -> None:
    live = replace(LIVE, unavailable=frozenset({"COKE"}))
    assert rejected([CartLine("COKE", 1)], live) == "unavailable"
    assert [i.sku for i in live.offerable().items()] == ["ZINGER"]
    # An unavailable item's whole category disappears when it was the only item.
    assert [c.name for c in live.offerable().categories] == ["Burgers"]


def test_switched_off_options_are_refused() -> None:
    cart = [CartLine("COKE", 1)]
    assert rejected(cart, settings=replace(SETTINGS, delivery_enabled=False)) == (
        "fulfilment_unavailable"
    )
    assert rejected(
        cart, fulfilment="takeaway", settings=replace(SETTINGS, takeaway_enabled=False)
    ) == ("fulfilment_unavailable")
    assert rejected(cart, settings=replace(SETTINGS, cod_enabled=False)) == "payment_unavailable"
    assert rejected(
        cart, payment_method="transfer", settings=replace(SETTINGS, tax_transfer_bps=None)
    ) == ("tax_not_configured")


def test_delivery_minimum_is_on_the_item_subtotal() -> None:
    settings = replace(SETTINGS, delivery_min_order_paisa=24000)
    assert rejected([CartLine("COKE", 1)], settings=settings) == "below_minimum"
    quote = price_cart(
        LIVE, settings, [CartLine("COKE", 2)], fulfilment="delivery", payment_method="cod"
    )
    assert quote.subtotal_paisa == 24000


def test_pickup_window() -> None:
    check_pickup_time(SETTINGS, NOW, NOW + timedelta(minutes=20))
    check_pickup_time(SETTINGS, NOW, NOW + timedelta(minutes=240))
    for minutes in (19, 241):
        with pytest.raises(QuoteRejected):
            check_pickup_time(SETTINGS, NOW, NOW + timedelta(minutes=minutes))


def test_cod_first_order_limit_and_transfer_window() -> None:
    assert cod_allowed(SETTINGS, 300000, first_order=True)
    assert not cod_allowed(SETTINGS, 300001, first_order=True)
    assert cod_allowed(SETTINGS, 900000, first_order=False)
    assert cod_allowed(
        replace(SETTINGS, cod_first_order_limit_paisa=None), 900000, first_order=True
    )
    assert not cod_allowed(replace(SETTINGS, cod_enabled=False), 100, first_order=False)
    assert transfer_deadline(SETTINGS, NOW) == NOW + timedelta(minutes=30)


def test_settings_changes_are_validated() -> None:
    assert apply_changes(SETTINGS, {"tax_cod_bps": None}).tax_cod_bps is None
    assert apply_changes(SETTINGS, {"delivery_enabled": False}).delivery_enabled is False
    for bad in (
        {"delivery_fee_paisa": True},
        {"delivery_fee_paisa": "100"},
        {"delivery_enabled": 1},
        {"transfer_window_minutes": 4},
        {"paused_until": None},
    ):
        with pytest.raises(InvalidInputError):
            apply_changes(SETTINGS, bad)


def test_capacity_slots_are_quarter_hours_in_pakistan_time() -> None:
    assert slot_start(NOW) == datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
    pkt = timedelta(hours=5)
    local = slot_start(datetime(2026, 10, 21, 19, 44, 59, tzinfo=UTC) - pkt) + pkt
    assert (local.hour, local.minute) == (19, 30)
