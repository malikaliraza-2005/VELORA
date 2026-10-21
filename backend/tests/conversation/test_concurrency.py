"""P6-T1: one message at a time per conversation.

Exit criterion: **two simultaneous messages in one chat are processed in order.**

- Through the real path: two messages arrive together and two workers race for them; the
  replies and the final state always match processing them in arrival order. Repeated.
- The handler itself: two transactions run `ConversationEngine.handle` for the same chat at
  the same moment, bypassing the inbox's own ordering. The first is held inside a slow AI
  call; the second must wait for it (advisory lock + row lock) and then see its result.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from tests.conversation.conftest import PRICES, FakeProposer, Shop, chat
from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from velora.ai.proposer import Proposal, ProposalRequest, ProposedLine
from velora.menu.model import format_rupees
from velora.messaging.inbox import InboxEvent

ROUNDS = 5


def test_two_simultaneous_messages_in_one_chat_are_processed_in_order(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        for _ in range(ROUNDS):
            customer = chat(env, db, shop)
            await customer.deliver("menu")
            await customer.deliver("2")
            workers = [customer.processor(), customer.processor()]
            done = await asyncio.gather(*(w.run_once(shop.tenant) for w in workers))
            assert sum(done) == 2
            replies = customer.new_replies()
            assert len(replies) == 2
            assert "Menu — reply with a number:" in replies[0]
            assert replies[1].startswith("Drinks\n1. Coca-Cola 345ml")
            state, context, _, _ = customer.conversation()
            assert (state, context["category"]) == ("category", "Drinks")
            env.clock.advance(timedelta(seconds=5))

    run(scenario)


def test_the_handler_serialises_two_messages_of_one_chat(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    entered = asyncio.Event()

    async def slow_model(request: ProposalRequest) -> Proposal:
        entered.set()
        await asyncio.sleep(0.5)
        return Proposal("order", (ProposedLine("COKE", 2),))

    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop, FakeProposer(slow_model))
        await customer.send("menu")
        engine = customer.engine

        def event(text: str) -> InboxEvent:
            payload = {
                "provider_message_id": text,
                "sender": customer.phone,
                "kind": "text",
                "text": text,
                "received_at": env.clock.now().isoformat(),
                "media": None,
            }
            return InboxEvent(uuid.uuid4(), shop.channel_id, "message", payload, 1, uuid.uuid4())

        order: list[str] = []

        async def handle(text: str) -> None:
            async with env.db.tenant(shop.tenant) as conn:
                await engine.handle(conn, shop.tenant, event(text))
                order.append(text)

        first = asyncio.create_task(handle("do coke dedo"))
        await entered.wait()  # the first holds the chat, inside the AI call
        second = asyncio.create_task(handle("yes"))
        await asyncio.gather(first, second)
        assert order == ["do coke dedo", "yes"]
        replies = customer.new_replies()
        assert replies[0].startswith(
            f"I understood:\n• 2 × Coca-Cola 345ml — {format_rupees(2 * PRICES['COKE'])}"
        )
        # The YES saw the proposal, so the cart has it.
        assert replies[1].startswith("Added: 2 × Coca-Cola 345ml")
        assert [line["sku"] for line in customer.conversation()[1]["cart"]] == ["COKE"]

    run(scenario)
