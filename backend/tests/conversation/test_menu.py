"""P6-T3: menu rendering and splitting.

Exit criteria covered here:
- **An unavailable item is never added**: not offered, not addable from an old list, and
  dropped from the cart if it runs out after being added.
- **A long menu is split within the limit** (provisional until V-09).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conversation.conftest import Shop, chat, publish_menu, set_available
from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from velora.conversation import render
from velora.messaging.provider import Capabilities


def test_an_unavailable_item_is_never_offered_or_added(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await customer.send("menu")
        (burgers,) = await customer.send("1")
        assert "1. Zinger Burger" in burgers
        # It runs out while the customer is looking at the list.
        set_available(db, shop, "ZINGER", False)
        replies = await customer.send("1")
        assert replies[0] == "Sorry, Zinger Burger is finished for today 😔"
        assert "Zinger" not in replies[1]
        assert replies[1].startswith("Burgers\n1. Beef Smash Burger")
        assert "cart" not in customer.conversation()[1]

    run(scenario)


def test_an_item_that_runs_out_is_removed_from_the_cart(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        for text in ("menu", "1", "2", "menu", "2", "1"):  # Beef Smash, Coca-Cola
            await customer.send(text)
        set_available(db, shop, "BEEF", False)
        (cart,) = await customer.send("checkout")
        assert "Removed Beef Smash Burger: Beef Smash Burger isn't available right now." in cart
        assert "Beef Smash Burger —" not in cart
        skus = [line["sku"] for line in customer.conversation()[1]["cart"]]
        assert skus == ["COKE"]

    run(scenario)


def test_a_long_menu_is_split_within_the_limit(run: Any, db: PgDatabase, shop: Shop) -> None:
    items = [
        (f"ITEM-{n}", f"Item number {n} with a long descriptive name", 10000 + n, [])
        for n in range(60)
    ]
    publish_menu(db, shop, [("Everything", items)], version_no=3)

    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await customer.send("menu")
        replies = await customer.send("1")
        limit = render.split_limit(env.fake.capabilities())
        assert len(replies) > 1
        assert all(len(r) <= limit for r in replies)
        joined = "\n".join(replies)
        for n in range(60):
            assert f"{n + 1}. Item number {n} with a long" in joined

    run(scenario)


@pytest.mark.parametrize("limit", [40, 100, 1000])
def test_split_keeps_every_line_and_respects_the_limit(limit: int) -> None:
    text = "\n".join(f"{n}. {'word ' * (n % 15)}end" for n in range(80))
    chunks = render.split(text, limit)
    assert all(0 < len(c) <= limit for c in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())


def test_the_split_limit_never_exceeds_the_provider_or_the_v09_cap() -> None:
    caps = Capabilities(
        buttons=False, lists=False, media=False, idempotent_send=False, max_text_length=300
    )
    assert render.split_limit(caps) == 300
    big = Capabilities(
        buttons=False, lists=False, media=False, idempotent_send=False, max_text_length=65536
    )
    assert render.split_limit(big) == render.SPLIT_LIMIT
