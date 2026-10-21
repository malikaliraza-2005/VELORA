"""P6-T6: human handoff and Resume bot.

- Each trigger hands over: 0, agent, insaan (and the Roman Urdu phrase), and two messages in
  a row the bot can't understand.
- While handed over, the bot sends nothing.
- Resume bot (staff, `CHATS_HANDLE`, audited) restores the bot.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.conversation.conftest import Customer, Shop, chat, rows
from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from velora.conversation import copy
from velora.conversation.service import ConversationService
from velora.identity.errors import ForbiddenError, NotFoundError
from velora.identity.permissions import Role
from velora.identity.sessions import StaffSession
from velora.identity.tokens import new_token


def staff(db: PgDatabase, shop: Shop, role: Role = Role.MANAGER) -> StaffSession:
    found = rows(
        db, "SELECT id FROM velora.staff_members WHERE tenant_id = %s LIMIT 1", shop.tenant.value
    )
    return StaffSession(
        tenant=shop.tenant,
        session_id=uuid.uuid4(),
        staff_id=found[0][0],
        role=role,
        display_name="Manager",
        token=new_token(shop.tenant),
    )


def conversation_id(db: PgDatabase, customer: Customer) -> uuid.UUID:
    found = rows(
        db,
        "SELECT c.id FROM velora.conversations c JOIN velora.customers cu ON cu.id = c.customer_id "
        "WHERE c.tenant_id = %s AND cu.phone_e164 = %s",
        customer.shop.tenant.value,
        customer.phone,
    )
    return uuid.UUID(str(found[0][0]))


@pytest.mark.parametrize("trigger", ["0", "agent", "Insaan", "insaan se baat karni hai", "STAFF"])
def test_each_trigger_hands_the_chat_to_staff(
    run: Any, db: PgDatabase, shop: Shop, trigger: str
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await customer.send("menu")
        assert await customer.send(trigger) == [copy.HANDOFF]
        state, _, reason, _ = customer.conversation()
        assert (state, reason) == ("handoff", "requested")

    run(scenario)


def test_two_messages_not_understood_hand_over(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)  # no AI: free text isn't understood
        await customer.send("menu")
        assert await customer.send("kal wala order") == [copy.NOT_UNDERSTOOD]
        assert await customer.send("woh hi") == [copy.HANDOFF_AFTER_MISSES]
        state, _, reason, _ = customer.conversation()
        assert (state, reason) == ("handoff", "not_understood")

    run(scenario)


def test_an_understood_message_resets_the_count(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await customer.send("menu")
        await customer.send("kal wala order")
        await customer.send("menu")
        assert await customer.send("woh hi") == [copy.NOT_UNDERSTOOD]
        assert customer.conversation()[0] != "handoff"

    run(scenario)


def test_while_handed_over_the_bot_sends_nothing_and_resume_restores_it(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        for text in ("menu", "2", "1"):  # a Coca-Cola in the cart
            await customer.send(text)
        await customer.send("agent")
        for text in ("hello?", "menu", "1", "cart", "status", "checkout", "YES"):
            assert await customer.send(text) == [], text
        service = ConversationService(env.db, env.clock)
        manager = staff(db, shop)
        waiting = await service.handoffs(manager)
        assert [w.conversation_id for w in waiting] == [conversation_id(db, customer)]
        assert waiting[0].customer_phone == customer.phone
        assert await service.resume_bot(manager, conversation_id(db, customer)) is True
        assert customer.new_replies() == [copy.RESUMED]
        state, context, reason, _ = customer.conversation()
        assert (state, reason) == ("idle", None)
        assert [line["sku"] for line in context["cart"]] == ["COKE"]  # the cart survives
        assert await service.handoffs(manager) == []
        # Resuming again does nothing.
        assert await service.resume_bot(manager, conversation_id(db, customer)) is False
        assert customer.new_replies() == []
        audit = rows(
            db,
            "SELECT actor_kind, actor_id, target_id, details->>'handoff_reason' "
            "FROM velora.audit_log WHERE tenant_id = %s AND action = 'conversation.bot_resumed'",
            shop.tenant.value,
        )
        assert audit == [("staff", manager.staff_id, conversation_id(db, customer), "requested")]
        # The bot answers again.
        (cart,) = await customer.send("cart")
        assert "Coca-Cola" in cart

    run(scenario)


def test_stop_still_works_while_handed_over(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await customer.send("0")
        (reply,) = await customer.send("STOP")
        assert reply.startswith("You won't receive more messages")
        assert customer.conversation()[3] is not None
        # Resuming an opted-out customer's chat sends them nothing.
        service = ConversationService(env.db, env.clock)
        assert await service.resume_bot(staff(db, shop), conversation_id(db, customer))
        assert customer.new_replies() == []

    run(scenario)


def test_only_chat_handlers_may_resume(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await customer.send("0")
        service = ConversationService(env.db, env.clock)
        for role in (Role.KITCHEN, Role.KITCHEN_DEVICE):
            with pytest.raises(ForbiddenError):
                await service.resume_bot(staff(db, shop, role), conversation_id(db, customer))
            with pytest.raises(ForbiddenError):
                await service.handoffs(staff(db, shop, role))
        with pytest.raises(NotFoundError):
            await service.resume_bot(staff(db, shop), uuid.uuid4())
        assert customer.conversation()[0] == "handoff"

    run(scenario)
