"""Server-side cart pricing (AD-20, P4-T6). P6 shows this quote; P7 re-runs it inside the
order transaction and compares.

Every amount comes from the published menu and the tenant's settings, never from AI output
or customer text. Pure functions: the caller loads the live menu and settings.

- Unit price = item price + the chosen options' extra charges; line total = unit x quantity.
- Delivery adds the flat fee and must reach the minimum order (on the item subtotal).
- Tax is the payment method's rate on the item subtotal, rounded half up to the paisa. If the
  rate isn't set, the quote is refused rather than silently untaxed. Whether tax also applies
  to the delivery fee, and the rates themselves, wait for the accountant (B3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from velora.menu.model import Item, format_rupees
from velora.menu.store import LiveMenu
from velora.ordering.settings import OrderingSettings

Fulfilment = Literal["delivery", "takeaway"]
PaymentMethod = Literal["cod", "transfer"]

MAX_LINES = 30
MAX_QUANTITY = 50


@dataclass(frozen=True)
class CartLine:
    sku: str
    quantity: int
    # (modifier group name, option name) pairs.
    choices: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class QuotedLine:
    sku: str
    name: str
    choices: tuple[tuple[str, str], ...]
    unit_price_paisa: int
    quantity: int
    line_total_paisa: int


@dataclass(frozen=True)
class Quote:
    menu_version_id: uuid.UUID
    fulfilment: Fulfilment
    payment_method: PaymentMethod
    lines: tuple[QuotedLine, ...]
    subtotal_paisa: int
    delivery_fee_paisa: int
    tax_bps: int
    tax_paisa: int
    total_paisa: int


class QuoteRejected(Exception):  # noqa: N818 - a refusal, not a fault
    """The cart can't be priced as it stands. The message is safe to show the customer."""

    def __init__(self, code: str, message: str, *, sku: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.sku = sku


def tax_for(subtotal_paisa: int, bps: int) -> int:
    return (subtotal_paisa * bps + 5000) // 10000


def price_cart(
    live: LiveMenu,
    settings: OrderingSettings,
    lines: list[CartLine],
    *,
    fulfilment: Fulfilment,
    payment_method: PaymentMethod,
) -> Quote:
    if not lines:
        raise QuoteRejected("empty_cart", "the cart is empty")
    if len(lines) > MAX_LINES:
        raise QuoteRejected("too_many_lines", f"a cart has at most {MAX_LINES} lines")
    if fulfilment == "delivery" and not settings.delivery_enabled:
        raise QuoteRejected("fulfilment_unavailable", "delivery isn't available right now")
    if fulfilment == "takeaway" and not settings.takeaway_enabled:
        raise QuoteRejected("fulfilment_unavailable", "takeaway isn't available right now")
    if payment_method == "cod" and not settings.cod_enabled:
        raise QuoteRejected("payment_unavailable", "cash on delivery isn't available")
    if payment_method == "transfer" and not settings.transfer_enabled:
        raise QuoteRejected("payment_unavailable", "bank or wallet transfer isn't available")
    if payment_method == "transfer" and settings.transfer_account_details is None:
        # P8-T3: the customer must be told where to pay.
        raise QuoteRejected("payment_unavailable", "bank or wallet transfer isn't set up yet")

    quoted = quote_lines(live, lines)
    subtotal = sum(q.line_total_paisa for q in quoted)
    fee = 0
    if fulfilment == "delivery":
        fee = settings.delivery_fee_paisa
        if subtotal < settings.delivery_min_order_paisa:
            minimum = format_rupees(settings.delivery_min_order_paisa)
            raise QuoteRejected("below_minimum", f"the minimum order for delivery is {minimum}")
    bps = settings.tax_cod_bps if payment_method == "cod" else settings.tax_transfer_bps
    if bps is None:
        raise QuoteRejected(
            "tax_not_configured", "this restaurant can't take orders with this payment method yet"
        )
    tax = tax_for(subtotal, bps)
    return Quote(
        menu_version_id=live.version.id,
        fulfilment=fulfilment,
        payment_method=payment_method,
        lines=quoted,
        subtotal_paisa=subtotal,
        delivery_fee_paisa=fee,
        tax_bps=bps,
        tax_paisa=tax,
        total_paisa=subtotal + fee + tax,
    )


def quote_lines(live: LiveMenu, lines: list[CartLine]) -> tuple[QuotedLine, ...]:
    """Price lines against the live menu without fulfilment or payment: the cart view and an
    AI proposal (P6) show these. Raises QuoteRejected for the first line that can't be priced."""
    return tuple(price_line(live, line) for line in lines)


def price_line(live: LiveMenu, line: CartLine) -> QuotedLine:
    sku = line.sku.strip().upper()
    item = live.item(sku)
    if item is None:
        raise QuoteRejected("unknown_item", "that item isn't on the menu", sku=sku)
    if not live.is_available(sku):
        raise QuoteRejected("unavailable", f"{item.name} isn't available right now", sku=sku)
    if not 1 <= line.quantity <= MAX_QUANTITY:
        raise QuoteRejected(
            "quantity", f"order between 1 and {MAX_QUANTITY} of {item.name}", sku=sku
        )
    unit = item.price_paisa + _choices_price(item, line.choices)
    return QuotedLine(sku, item.name, line.choices, unit, line.quantity, unit * line.quantity)


def _choices_price(item: Item, choices: tuple[tuple[str, str], ...]) -> int:
    groups = {g.name.casefold(): g for g in item.groups}
    picked: dict[str, set[str]] = {}
    extra = 0
    for group_name, option_name in choices:
        group = groups.get(group_name.casefold())
        option = (
            None
            if group is None
            else next(
                (o for o in group.options if o.name.casefold() == option_name.casefold()), None
            )
        )
        if group is None or option is None:
            raise QuoteRejected(
                "invalid_choice", f"{option_name!r} isn't an option for {item.name}", sku=item.sku
            )
        chosen = picked.setdefault(group.name.casefold(), set())
        if option.name.casefold() in chosen:
            raise QuoteRejected(
                "invalid_choice", f"{option.name} was chosen twice for {item.name}", sku=item.sku
            )
        chosen.add(option.name.casefold())
        extra += option.price_delta_paisa
    for group in item.groups:
        count = len(picked.get(group.name.casefold(), ()))
        if count < group.min_select:
            raise QuoteRejected(
                "choice_required", f"choose {group.name} for {item.name}", sku=item.sku
            )
        if count > group.max_select:
            raise QuoteRejected(
                "invalid_choice",
                f"choose at most {group.max_select} for {group.name} on {item.name}",
                sku=item.sku,
            )
    return extra


# Rules applied around the quote (pickup in P6, COD and transfer windows in P8) -------------


def pickup_window(settings: OrderingSettings, now: datetime) -> tuple[datetime, datetime]:
    return (
        now + timedelta(minutes=settings.pickup_min_lead_minutes),
        now + timedelta(minutes=settings.pickup_max_ahead_minutes),
    )


def check_pickup_time(settings: OrderingSettings, now: datetime, pickup_at: datetime) -> None:
    earliest, latest = pickup_window(settings, now)
    if not earliest <= pickup_at <= latest:
        raise QuoteRejected(
            "pickup_time",
            f"pickup must be between {settings.pickup_min_lead_minutes} and "
            f"{settings.pickup_max_ahead_minutes} minutes from now",
        )


def cod_allowed(
    settings: OrderingSettings, total_paisa: int, *, first_order: bool, blocked: bool = False
) -> bool:
    """AD-03, P8-T2: a blocklisted customer can't pay cash on delivery, and a customer's first
    order (no completed order yet, AD-32) may not exceed the tenant's limit."""
    if not settings.cod_enabled or blocked:
        return False
    limit = settings.cod_first_order_limit_paisa
    return not (first_order and limit is not None and total_paisa > limit)


def transfer_deadline(settings: OrderingSettings, placed_at: datetime) -> datetime:
    """When an unpaid transfer order expires (P8)."""
    return placed_at + timedelta(minutes=settings.transfer_window_minutes)
