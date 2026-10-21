"""Fixtures for the P7 order lifecycle tests.

They reuse the P6 conversation fixtures: each test gets its own restaurant (`shop`) with the
test menu published, and a `Customer` drives a real chat through the FakeProvider, the inbox
and the conversation engine. Orders are then moved as staff with `lifecycle.transition`.
"""

from __future__ import annotations

import uuid
from typing import Any

from tests.conversation.conftest import (  # noqa: F401 - fixtures
    DELIVERY_FEE,
    PRICES,
    TAX_COD_BPS,
    TAX_TRANSFER_BPS,
    Customer,
    Shop,
    chat,
    clock,
    cluster,
    db,
    fake,
    rows,
    run,
    shop,
    tenants,
)
from tests.db.conftest import PgDatabase
from velora.conversation.engine import checkout_of
from velora.ordering.pricing import tax_for
from velora.orders.placement import Checkout

ADDRESS = "House 12, Street 4, DHA Phase 5"
BEEF = PRICES["BEEF"]
BEEF_COD_TOTAL = BEEF + DELIVERY_FEE + tax_for(BEEF, TAX_COD_BPS)
BEEF_TRANSFER_TOTAL = BEEF + DELIVERY_FEE + tax_for(BEEF, TAX_TRANSFER_BPS)


async def to_summary(
    customer: Customer, *, payment: str = "cod", fulfilment: str = "delivery", coke: bool = False
) -> str:
    """Put a Beef Smash Burger (and a Coca-Cola) in the cart and check out to the summary."""
    for text in ("menu", "1", "2"):
        await customer.send(text)
    if coke:
        for text in ("menu", "2", "1"):
            await customer.send(text)
    await customer.send("checkout")
    if fulfilment == "delivery":
        for text in ("1", ADDRESS, "skip"):
            await customer.send(text)
    else:
        for text in ("2", "1"):  # takeaway, as soon as possible
            await customer.send(text)
    (summary,) = await customer.send("1" if payment == "cod" else "2")
    assert summary.startswith("🧾 Order summary"), summary
    return summary


def ids_of(pg: PgDatabase, customer: Customer) -> tuple[uuid.UUID, uuid.UUID]:
    """(conversation id, customer id) of a chat."""
    (row,) = rows(
        pg,
        "SELECT c.id, cu.id FROM velora.conversations c "
        "JOIN velora.customers cu ON cu.id = c.customer_id "
        "WHERE c.tenant_id = %s AND cu.phone_e164 = %s",
        customer.shop.tenant.value,
        customer.phone,
    )
    return row[0], row[1]


def summary_of(customer: Customer) -> tuple[str, Checkout]:
    """The nonce and the checkout a chat in the summary state would confirm."""
    state, context, _, _ = customer.conversation()
    assert state == "summary"
    return context["quote"]["nonce"], checkout_of(context)


def orders_of(pg: PgDatabase, customer: Customer) -> list[tuple[Any, ...]]:
    """(id, status, total, possible_duplicate, payment_due_at) of a customer's orders, oldest
    first."""
    return rows(
        pg,
        "SELECT o.id, o.status, o.total_paisa, o.possible_duplicate, o.payment_due_at "
        "FROM velora.orders o JOIN velora.customers cu ON cu.id = o.customer_id "
        "WHERE o.tenant_id = %s AND cu.phone_e164 = %s ORDER BY o.created_at, o.order_number",
        customer.shop.tenant.value,
        customer.phone,
    )


def staff_id_of(pg: PgDatabase, restaurant: Shop) -> uuid.UUID:
    (row,) = rows(
        pg,
        "SELECT id FROM velora.staff_members WHERE tenant_id = %s LIMIT 1",
        restaurant.tenant.value,
    )
    staff: uuid.UUID = row[0]
    return staff


def slot_count(pg: PgDatabase, restaurant: Shop) -> int:
    found = rows(
        pg,
        "SELECT coalesce(sum(orders_count), 0) FROM velora.order_capacity_slots "
        "WHERE tenant_id = %s",
        restaurant.tenant.value,
    )
    return int(found[0][0])
