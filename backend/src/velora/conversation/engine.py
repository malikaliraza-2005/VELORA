"""The conversation engine: one inbound customer message in, a state change and replies out
(P6-T1, T2, T4, T5, T6; AD-20, AD-24).

It is the inbox handler for `message` events (`ConversationEngine.handle`), so it runs inside
the inbox transaction: the state change, the opt-out and the queued replies commit together
with the event being marked done, exactly once (P5-T3).

**One message at a time per conversation (P6-T1).** The inbox already hands out one
customer's events in arrival order. On top of that, `handle` takes a transaction-scoped
advisory lock on (channel, customer) and locks the conversation row, so even two workers
that somehow held messages from the same chat at once would apply them one after the other.

**Order of checks for each message:**
1. Opted out: only START is answered; everything else is silent.
2. Exactly "STOP" opts out (AD-24). "CANCEL" never does; it clears the cart.
3. Handed over to staff: the bot sends nothing until Resume bot.
4. Voice notes get a request to type (AD-14). Other media get a request to type.
5. Global commands: 0 / agent / insaan (handoff), CANCEL, status, MENU, CART, CHECKOUT.
6. The current state's own input: a number from the menu, an option, an address...
7. Anything else is free text: the AI may turn it into a *proposal* (P6-T4), which the
   customer sees rendered from database values and must accept with YES.

Two messages in a row that the bot can't understand hand the chat to staff.

**Money (AD-20).** The cart in `context` holds SKUs, quantities and option names only. Every
amount shown is re-priced from the published menu and settings at that moment; the summary is
a fixed template over `price_cart`'s quote. AI output never reaches a total.

**Orders (P7).** Showing the summary issues a single-use YES nonce bound to the chat, the
customer and the exact checkout (`velora.orders.placement`). YES places the order inside a
savepoint of this transaction: if anything is refused or fails, nothing of the order is kept,
and the customer gets a clear message (an updated summary to confirm, the cart, or an apology).
CANCEL clears a cart being built; with nothing being built it cancels the customer's placed
order, if it hasn't been accepted yet.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from velora.ai.proposer import Proposal, ProposedLine, Proposer, build_request
from velora.clock import Clock
from velora.conversation import copy, render
from velora.conversation.pktime import format_time, parse_pickup
from velora.db import TenantId
from velora.identity.errors import expect_row
from velora.menu.model import Category, Item, Menu, format_rupees
from velora.menu.store import LiveMenu, live_menu
from velora.messaging.inbox import InboxEvent
from velora.messaging.outbox import OutboundText, enqueue
from velora.messaging.provider import Capabilities, WhatsAppProvider
from velora.ordering import gate
from velora.ordering.pricing import (
    MAX_LINES,
    CartLine,
    Fulfilment,
    PaymentMethod,
    QuotedLine,
    QuoteRejected,
    check_pickup_time,
    cod_allowed,
    pickup_window,
    price_cart,
    price_line,
)
from velora.ordering.settings import OrderingSettings, load_settings
from velora.orders import lifecycle, placement
from velora.redaction import error_text

log = logging.getLogger(__name__)

SUMMARY_TTL = timedelta(minutes=10)
MAX_MISSES = 2
PICKUP_OPTIONS = 5
PICKUP_STEP = timedelta(minutes=15)
ADDRESS_MIN, ADDRESS_MAX, NOTE_MAX = 10, 300, 200
# Used when a channel's provider isn't registered in this process (it can't send anyway).
FALLBACK_CAPABILITIES = Capabilities(
    buttons=False, lists=False, media=False, idempotent_send=False, max_text_length=1000
)

HANDOFF_WORDS = frozenset({"0", "agent", "insaan", "insan", "staff", "human", "operator"})
HANDOFF_PHRASES = (
    "insaan se baat",
    "insan se baat",
    "staff se baat",
    "talk to staff",
    "talk to a human",
    "talk to human",
    "real person",
)
# A short message that opens with a greeting or asks for the menu shows the menu.
GREETINGS = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "salam",
        "salaam",
        "aoa",
        "assalam",
        "asalam",
        "assalamualaikum",
        "assalamu",
        "start",
    }
)
SHORT_MESSAGE_WORDS = 8
CHECKOUT_KEYS = (
    "fulfilment",
    "fulfilment_options",
    "address",
    "address_message",
    "delivery_note",
    "pickup_at",
    "pickup_options",
    "payment_method",
    "payment_options",
    "quote",
)
_PICK = re.compile(r"^(\d{1,3})(?:\s*[x×*]\s*(\d{1,2}))?$")
_REMOVE = re.compile(r"^remove\s+(\d{1,2})$")
PAYMENT_WORDS: dict[str, PaymentMethod] = {
    "cod": "cod",
    "cash": "cod",
    "cash on delivery": "cod",
    "transfer": "transfer",
    "bank": "transfer",
    "online": "transfer",
    "easypaisa": "transfer",
    "jazzcash": "transfer",
}


def normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def _asks_for_menu(words: str) -> bool:
    tokens = re.findall(r"[a-z]+", words)
    if not tokens or len(tokens) > SHORT_MESSAGE_WORDS:
        return False
    return "menu" in tokens or (tokens[0] in GREETINGS and not any(t.isdigit() for t in words))


def lock_key(channel_id: uuid.UUID, phone: str) -> str:
    return f"conversation:{channel_id}:{phone}"


async def lock_conversation(conn: AsyncConnection[Any], channel_id: uuid.UUID, phone: str) -> None:
    """Serialise all work on one chat until the transaction ends (P6-T1)."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key(channel_id, phone),)
    )


def _lines(raw: Any) -> list[CartLine]:
    return [
        CartLine(
            str(r["sku"]), int(r["quantity"]), tuple((str(g), str(o)) for g, o in r["choices"])
        )
        for r in raw or []
    ]


def _store(lines: list[CartLine]) -> list[dict[str, Any]]:
    return [
        {"sku": ln.sku, "quantity": ln.quantity, "choices": [list(c) for c in ln.choices]}
        for ln in lines
    ]


def _same(a: CartLine, b: CartLine) -> bool:
    def key(line: CartLine) -> tuple[str, frozenset[tuple[str, str]]]:
        return line.sku, frozenset((g.casefold(), o.casefold()) for g, o in line.choices)

    return key(a) == key(b)


def checkout_of(context: Mapping[str, Any]) -> placement.Checkout:
    """What a chat in the summary state is about to confirm (P7), from its context."""
    pickup_at = context.get("pickup_at")
    return placement.Checkout(
        lines=tuple(_lines(context.get("cart"))),
        fulfilment=context["fulfilment"],
        payment_method=context["payment_method"],
        address=context.get("address"),
        address_message=context.get("address_message"),
        delivery_note=context.get("delivery_note"),
        pickup_at=None if pickup_at is None else datetime.fromisoformat(pickup_at),
    )


@dataclass
class Chat:
    tenant: TenantId
    conversation_id: uuid.UUID
    channel_id: uuid.UUID
    customer_id: uuid.UUID
    phone: str
    state: str
    context: dict[str, Any]
    opted_out: bool
    first_contact: bool


@dataclass
class _Turn:
    chat: Chat
    now: datetime
    state: str
    context: dict[str, Any]
    # Messages in a row not understood: before this one, and including it if it wasn't.
    prior_misses: int = 0
    misses: int = 0
    replies: list[str] = field(default_factory=list)
    # True: opt out; False: opt back in; None: no change.
    opt_out: bool | None = None
    handoff_reason: str | None = None
    disclose: bool = False

    def say(self, *texts: str) -> None:
        self.replies.extend(t for t in texts if t)

    def cart(self) -> list[CartLine]:
        return _lines(self.context.get("cart"))

    def leave_checkout(self) -> None:
        for key in CHECKOUT_KEYS:
            self.context.pop(key, None)


class _Data:
    """Reads for one message, each done at most once, inside the handler's transaction."""

    def __init__(self, conn: AsyncConnection[Any], tenant: TenantId, now: datetime) -> None:
        self.conn = conn
        self.tenant = tenant
        self.now = now
        self._live: LiveMenu | None = None
        self._live_loaded = False
        self._settings: OrderingSettings | None = None
        self._restaurant: str | None = None

    async def live(self) -> LiveMenu | None:
        if not self._live_loaded:
            self._live, self._live_loaded = await live_menu(self.conn), True
        return self._live

    async def offerable(self) -> Menu | None:
        live = await self.live()
        menu = None if live is None else live.offerable()
        return menu if menu is not None and menu.categories else None

    async def settings(self) -> OrderingSettings:
        if self._settings is None:
            self._settings = await load_settings(self.conn, self.tenant)
        return self._settings

    async def restaurant(self) -> str:
        if self._restaurant is None:
            cur = await self.conn.execute("SELECT display_name FROM velora.tenants")
            (self._restaurant,) = expect_row(await cur.fetchone())
        return self._restaurant

    async def gate(self) -> gate.GateDecision:
        return await gate.check_new_cart(self.conn, self.tenant, self.now)

    async def has_orders(self, customer_id: uuid.UUID) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM velora.orders WHERE customer_id = %s LIMIT 1", (customer_id,)
        )
        return await cur.fetchone() is not None


class ConversationEngine:
    def __init__(
        self,
        clock: Clock,
        providers: Mapping[str, WhatsAppProvider],
        proposer: Proposer | None = None,
        *,
        ai_timeout: float = 8.0,
    ) -> None:
        self._clock = clock
        self._providers = providers
        self._proposer = proposer
        self._ai_timeout = ai_timeout

    # The inbox handler ---------------------------------------------------------------------

    async def handle(self, conn: AsyncConnection[Any], tenant: TenantId, event: InboxEvent) -> None:
        payload = event.payload
        sender = str(payload["sender"])
        now = self._clock.now()
        await lock_conversation(conn, event.channel_id, sender)
        chat = await self._load(conn, tenant, event.channel_id, sender, now)
        turn = _Turn(chat, now, chat.state, dict(chat.context))
        turn.prior_misses = int(turn.context.pop("misses", 0))
        data = _Data(conn, tenant, now)
        await self._step(
            turn,
            data,
            kind=str(payload.get("kind") or "text"),
            text=str(payload.get("text") or ""),
            message_id=str(payload.get("provider_message_id") or ""),
        )
        await self._save(conn, tenant, turn, data, event)

    async def _load(
        self,
        conn: AsyncConnection[Any],
        tenant: TenantId,
        channel_id: uuid.UUID,
        phone: str,
        now: datetime,
    ) -> Chat:
        cur = await conn.execute(
            "INSERT INTO velora.customers (tenant_id, phone_e164, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, phone_e164) "
            "DO UPDATE SET updated_at = EXCLUDED.updated_at RETURNING id, opted_out_at",
            (tenant.value, phone, now, now),
        )
        customer_id, opted_out_at = expect_row(await cur.fetchone())
        await conn.execute(
            "INSERT INTO velora.conversations (tenant_id, channel_id, customer_id, created_at, "
            "updated_at) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (tenant_id, channel_id, customer_id) DO NOTHING",
            (tenant.value, channel_id, customer_id, now, now),
        )
        cur = await conn.execute(
            "SELECT id, state, context, last_outbound_at FROM velora.conversations "
            "WHERE channel_id = %s AND customer_id = %s FOR UPDATE",
            (channel_id, customer_id),
        )
        conversation_id, state, context, last_outbound_at = expect_row(await cur.fetchone())
        return Chat(
            tenant,
            conversation_id,
            channel_id,
            customer_id,
            phone,
            state,
            dict(context),
            opted_out_at is not None,
            last_outbound_at is None,
        )

    async def _save(
        self,
        conn: AsyncConnection[Any],
        tenant: TenantId,
        turn: _Turn,
        data: _Data,
        event: InboxEvent,
    ) -> None:
        chat, now = turn.chat, turn.now
        if turn.misses:
            turn.context["misses"] = turn.misses
        replies = list(turn.replies)
        if replies and turn.disclose:
            replies[0] = copy.disclosure(await data.restaurant()) + "\n\n" + replies[0]
        if turn.opt_out is not None:
            await conn.execute(
                "UPDATE velora.customers SET opted_out_at = %s, updated_at = %s WHERE id = %s",
                (now if turn.opt_out else None, now, chat.customer_id),
            )
        if turn.opt_out:
            # Replies still queued for this customer are not sent after STOP.
            await conn.execute(
                "UPDATE velora.outbox_messages SET status = 'cancelled' "
                "WHERE channel_id = %s AND ordering_key = %s AND purpose = 'conversational' "
                "AND status IN ('pending', 'waiting_for_channel')",
                (chat.channel_id, chat.phone),
            )
        limit = render.split_limit(await self._capabilities(conn, chat.channel_id))
        chunks = [chunk for reply in replies for chunk in render.split(reply, limit)]
        for n, chunk in enumerate(chunks):
            await enqueue(
                conn,
                tenant,
                channel_id=chat.channel_id,
                message=OutboundText(chat.phone, chunk),
                idempotency_key=f"conversation:{event.id}:{n}",
                purpose="conversational",
                at=now,
                conversation_id=chat.conversation_id,
            )
        handed_over = turn.state == "handoff"
        await conn.execute(
            "UPDATE velora.conversations SET state = %(state)s, context = %(context)s, "
            "state_version = state_version + 1, last_inbound_at = %(now)s, "
            "last_outbound_at = CASE WHEN %(sent)s THEN %(now)s ELSE last_outbound_at END, "
            "handoff_at = CASE WHEN %(handoff)s THEN COALESCE(handoff_at, %(now)s) END, "
            "handoff_reason = CASE WHEN %(handoff)s "
            "THEN COALESCE(handoff_reason, %(reason)s) END, "
            "updated_at = %(now)s WHERE id = %(id)s",
            {
                "state": turn.state,
                "context": Jsonb(turn.context),
                "now": now,
                "sent": bool(chunks),
                "handoff": handed_over,
                "reason": turn.handoff_reason or "requested",
                "id": chat.conversation_id,
            },
        )

    async def _capabilities(
        self, conn: AsyncConnection[Any], channel_id: uuid.UUID
    ) -> Capabilities:
        cur = await conn.execute(
            "SELECT provider FROM velora.channels WHERE id = %s", (channel_id,)
        )
        (provider,) = expect_row(await cur.fetchone())
        adapter = self._providers.get(provider)
        return FALLBACK_CAPABILITIES if adapter is None else adapter.capabilities()

    # Dispatch ------------------------------------------------------------------------------

    async def _step(
        self, turn: _Turn, data: _Data, *, kind: str, text: str, message_id: str
    ) -> None:
        chat = turn.chat
        words = normalise(text)
        exact = text.strip()
        if chat.opted_out:
            if exact.upper() == "START":
                turn.opt_out, turn.disclose = False, True
                turn.state, turn.context = "idle", {}
                await self._show_menu(turn, data)
            return
        # AD-24: only exactly STOP opts out; CANCEL and anything else never do.
        if exact.upper() == "STOP":
            turn.opt_out = True
            turn.say(copy.opted_out(await data.restaurant()))
            return
        if turn.state == "handoff":
            return
        turn.disclose = chat.first_contact
        if kind == "audio":
            turn.say(copy.VOICE_NOTE)
            return
        if kind == "location" and turn.state == "address":
            await self._address(turn, data, None, message_id)
            return
        if kind != "text" or not words:
            turn.say(copy.TEXT_ONLY)
            return
        if words in HANDOFF_WORDS or any(p in words for p in HANDOFF_PHRASES):
            self._handoff(turn, "requested")
        elif words == "cancel":
            await self._cancel(turn, data)
        elif words == "status":
            await self._status(turn, data)
        elif _asks_for_menu(words):
            await self._show_menu(turn, data)
        elif words == "cart":
            await self._show_cart(turn, data)
        elif words == "checkout":
            await self._checkout(turn, data)
        else:
            await self._state_input(turn, data, text, words)

    async def _state_input(self, turn: _Turn, data: _Data, text: str, words: str) -> None:
        state = turn.state
        pick = _PICK.match(words)
        if state == "choosing":
            await self._choose(turn, data, words)
        elif state == "fulfilment":
            await self._pick_fulfilment(turn, data, words)
        elif state == "address":
            await self._address(turn, data, text, None)
        elif state == "delivery_note":
            note = None if words == "skip" else " ".join(text.split())[:NOTE_MAX]
            turn.context["delivery_note"] = note
            await self._ask_payment(turn, data)
        elif state == "pickup_time":
            await self._pick_pickup(turn, data, text, words)
        elif state == "payment":
            await self._pick_payment(turn, data, words)
        elif state == "summary" and words == "yes":
            await self._confirm(turn, data)
        elif state == "proposal" and words == "yes":
            lines = _lines(turn.context.pop("proposal", []))
            await self._add(turn, data, lines)
        elif words == "yes":
            turn.say(copy.NOTHING_TO_CONFIRM)
        elif (removal := _REMOVE.match(words)) and turn.context.get("cart"):
            await self._remove(turn, data, int(removal.group(1)))
        elif pick and state == "category":
            await self._pick_item(turn, data, int(pick.group(1)), int(pick.group(2) or 1))
        elif pick and state in ("idle", "browsing", "proposal") and pick.group(2) is None:
            if turn.context.get("menu") and state != "idle":
                await self._pick_category(turn, data, int(pick.group(1)))
            else:
                await self._show_menu(turn, data)
        elif state in ("summary",):
            self._miss(turn)
        else:
            await self._free_text(turn, data, text)

    # Fixed commands (P6-T2, T6) ------------------------------------------------------------

    def _miss(self, turn: _Turn) -> None:
        turn.misses = turn.prior_misses + 1
        if turn.misses >= MAX_MISSES:
            self._handoff(turn, "not_understood")
        else:
            turn.say(copy.NOT_UNDERSTOOD)

    def _handoff(self, turn: _Turn, reason: str) -> None:
        turn.state, turn.handoff_reason, turn.misses = "handoff", reason, 0
        turn.context.pop("proposal", None)
        turn.context.pop("pending", None)
        turn.leave_checkout()
        turn.say(copy.HANDOFF if reason == "requested" else copy.HANDOFF_AFTER_MISSES)

    async def _cancel(self, turn: _Turn, data: _Data) -> None:
        """CANCEL clears what is being built. With nothing being built, it cancels the
        customer's open order, only before the restaurant accepts it (P7-T3). It never opts
        out (AD-24)."""
        ctx = turn.context
        building = bool(ctx.get("cart") or ctx.get("proposal") or ctx.get("pending"))
        turn.state = "idle"
        turn.context = {}
        order = await self._open_order(turn, data)
        cancellable = order is not None and self._customer_can_cancel(order)
        if building or order is None:
            turn.say(
                copy.open_order_kept(order.reference)
                if order is not None and cancellable
                else copy.CART_CLEARED
            )
            return
        if not cancellable:
            turn.say(copy.cannot_cancel(order.reference, order.status))
            return
        try:
            async with data.conn.transaction():
                await lifecycle.transition(
                    data.conn,
                    data.tenant,
                    order.id,
                    to="cancelled",
                    actor=lifecycle.Actor("customer", turn.chat.customer_id),
                    now=turn.now,
                    expected=order.status,
                )
        except lifecycle.StaleTransitionError:
            # Staff moved it first (e.g. accepted it); tell the customer where it is now.
            current = await lifecycle.load_order(data.conn, order.id)
            turn.say(copy.cannot_cancel(order.reference, current.status if current else ""))

    @staticmethod
    def _customer_can_cancel(order: lifecycle.OrderRef) -> bool:
        return "cancelled" in lifecycle.next_statuses(
            order.status,
            "customer",
            payment_method=order.payment_method,
            fulfilment=order.fulfilment,
        )

    async def _open_order(self, turn: _Turn, data: _Data) -> lifecycle.OrderRef | None:
        """The customer's latest order that hasn't ended."""
        cur = await data.conn.execute(
            "SELECT id FROM velora.orders WHERE customer_id = %s AND NOT status = ANY(%s) "
            "ORDER BY created_at DESC LIMIT 1",
            (turn.chat.customer_id, sorted(lifecycle.TERMINAL)),
        )
        row = await cur.fetchone()
        return None if row is None else await lifecycle.load_order(data.conn, row[0])

    async def _status(self, turn: _Turn, data: _Data) -> None:
        cur = await data.conn.execute(
            "SELECT o.order_number, o.status, o.updated_at, t.order_number_prefix "
            "FROM velora.orders o JOIN velora.tenants t ON t.id = o.tenant_id "
            "WHERE o.customer_id = %s ORDER BY o.created_at DESC LIMIT 1",
            (turn.chat.customer_id,),
        )
        row = await cur.fetchone()
        if row is None:
            turn.say(copy.NO_ORDERS)
            return
        number, status, since, prefix = row
        turn.say(copy.order_status(lifecycle.order_reference(prefix, number), status, since))

    # Menu and cart (P6-T3) -----------------------------------------------------------------

    async def _show_menu(self, turn: _Turn, data: _Data) -> None:
        menu = await data.offerable()
        for key in ("pending", "proposal", "category", "items"):
            turn.context.pop(key, None)
        turn.leave_checkout()
        if menu is None:
            turn.state = "idle"
            turn.context.pop("menu", None)
            turn.say(copy.NO_MENU)
            return
        turn.state = "browsing"
        turn.context["menu"] = [c.name for c in menu.categories]
        turn.say(render.top_menu(menu))

    def _show_category(self, turn: _Turn, category: Category) -> None:
        turn.state = "category"
        turn.context["category"] = category.name
        turn.context["items"] = [item.sku for item in category.items]
        turn.say(render.category(category))

    async def _pick_category(self, turn: _Turn, data: _Data, number: int) -> None:
        names: list[str] = turn.context.get("menu") or []
        menu = await data.offerable()
        if not 1 <= number <= len(names):
            self._miss(turn)
            return
        found = (
            None
            if menu is None
            else next((c for c in menu.categories if c.name == names[number - 1]), None)
        )
        if found is None:
            await self._show_menu(turn, data)
            return
        self._show_category(turn, found)

    async def _unavailable(self, turn: _Turn, data: _Data, item: Item) -> None:
        """A3: the item is finished; offer what is left in its category."""
        turn.say(f"Sorry, {item.name} is finished for today 😔")
        menu = await data.offerable()
        home = (
            None
            if menu is None
            else next((c for c in menu.categories if c.name == turn.context.get("category")), None)
        )
        if home is None:
            await self._show_menu(turn, data)
        else:
            self._show_category(turn, home)

    async def _pick_item(self, turn: _Turn, data: _Data, number: int, quantity: int) -> None:
        skus: list[str] = turn.context.get("items") or []
        live = await data.live()
        if live is None or not 1 <= number <= len(skus):
            self._miss(turn)
            return
        item = live.item(skus[number - 1])
        if item is None or not live.is_available(item.sku):
            if item is None:
                await self._show_menu(turn, data)
            else:
                await self._unavailable(turn, data, item)
            return
        if item.groups:
            turn.state = "choosing"
            turn.context["pending"] = {
                "sku": item.sku,
                "quantity": quantity,
                "choices": [],
                "group": 0,
            }
            turn.say(render.group_question(item, item.groups[0]))
            return
        await self._add(turn, data, [CartLine(item.sku, quantity)])

    async def _choose(self, turn: _Turn, data: _Data, words: str) -> None:
        pending = turn.context.get("pending") or {}
        live = await data.live()
        item = None if live is None else live.item(str(pending.get("sku")))
        if live is None or item is None or not live.is_available(item.sku):
            turn.context.pop("pending", None)
            if item is None:
                await self._show_menu(turn, data)
            else:
                await self._unavailable(turn, data, item)
            return
        group = item.groups[int(pending["group"])]
        picks: list[int] = []
        if words != "skip" or group.min_select > 0:
            parts = [p for p in re.split(r"[,\s]+", words) if p]
            if not parts or not all(p.isdigit() for p in parts):
                self._miss(turn)
                if turn.state == "choosing":
                    turn.say(render.group_question(item, group))
                return
            picks = [int(p) for p in parts]
        valid = (
            all(1 <= p <= len(group.options) for p in picks)
            and len(set(picks)) == len(picks)
            and group.min_select <= len(picks) <= group.max_select
        )
        if not valid:
            turn.say(copy.CHOOSE_NUMBER, render.group_question(item, group))
            return
        pending["choices"] += [[group.name, group.options[p - 1].name] for p in picks]
        pending["group"] = int(pending["group"]) + 1
        if pending["group"] < len(item.groups):
            turn.context["pending"] = pending
            turn.say(render.group_question(item, item.groups[pending["group"]]))
            return
        turn.context.pop("pending", None)
        line = CartLine(
            item.sku, int(pending["quantity"]), tuple((g, o) for g, o in pending["choices"])
        )
        await self._add(turn, data, [line])

    async def _add(self, turn: _Turn, data: _Data, lines: list[CartLine]) -> None:
        """Add lines to the cart, priced by the server. An empty cart is a new cart, so the
        gate (pause, capacity, channel, global pause) is checked first."""
        live = await data.live()
        cart = turn.cart()
        turn.state = "browsing"
        if live is None or not lines:
            await self._show_menu(turn, data)
            return
        if not cart:
            decision = await data.gate()
            if not decision.open:
                turn.say(copy.closed(await data.restaurant(), decision.reason, decision.reopens_at))
                return
        merged = list(cart)
        for line in lines:
            at = next((i for i, existing in enumerate(merged) if _same(existing, line)), None)
            if at is None:
                merged.append(line)
            else:
                merged[at] = CartLine(
                    merged[at].sku, merged[at].quantity + line.quantity, merged[at].choices
                )
        if len(merged) > MAX_LINES:
            turn.say(
                f"A cart can have at most {MAX_LINES} different items. Reply CART to review it."
            )
            return
        try:
            for line in merged:
                price_line(live, line)
            added = tuple(price_line(live, line) for line in lines)
        except QuoteRejected as exc:
            turn.say(f"Sorry, {exc}. Reply MENU to choose again.")
            return
        turn.context["cart"] = _store(merged)
        turn.say(render.added(added))

    async def _priced_cart(
        self, turn: _Turn, data: _Data
    ) -> tuple[tuple[QuotedLine, ...], list[str]]:
        """The cart re-priced now. Lines that can no longer be ordered are removed, with a
        note saying so."""
        live = await data.live()
        kept: list[CartLine] = []
        quoted: list[QuotedLine] = []
        notes: list[str] = []
        for line in turn.cart():
            item = None if live is None else live.item(line.sku)
            try:
                if live is None:
                    raise QuoteRejected("no_menu", "the menu isn't available")
                quoted.append(price_line(live, line))
                kept.append(line)
            except QuoteRejected as exc:
                name = item.name if item is not None else line.sku
                notes.append(f"Removed {name}: {exc}.")
        turn.context["cart"] = _store(kept)
        return tuple(quoted), notes

    async def _show_cart(self, turn: _Turn, data: _Data) -> None:
        turn.state = "browsing"
        turn.leave_checkout()
        quoted, notes = await self._priced_cart(turn, data)
        if not quoted:
            turn.say(*notes, copy.CART_EMPTY)
            return
        turn.say(render.cart(quoted, notes))

    async def _remove(self, turn: _Turn, data: _Data, number: int) -> None:
        cart = turn.cart()
        if not 1 <= number <= len(cart):
            turn.say(copy.CHOOSE_NUMBER)
            return
        del cart[number - 1]
        turn.context["cart"] = _store(cart)
        await self._show_cart(turn, data)

    # Free text and the AI (P6-T4) ----------------------------------------------------------

    async def _free_text(self, turn: _Turn, data: _Data, text: str) -> None:
        settings = await data.settings()
        menu = await data.offerable()
        if self._proposer is None or not settings.ai_enabled or menu is None:
            self._miss(turn)
            return
        request = build_request(text, menu)
        try:
            proposal = await asyncio.wait_for(self._proposer.propose(request), self._ai_timeout)
        except Exception as exc:
            # Timeout or error: the numbered menu always works (P6-T4).
            log.warning("AI proposal failed, showing the menu: %s", error_text(exc))
            turn.say(copy.AI_FALLBACK)
            await self._show_menu(turn, data)
            return
        await self._apply_proposal(turn, data, proposal)

    async def _apply_proposal(self, turn: _Turn, data: _Data, proposal: Proposal) -> None:
        intent = proposal.intent
        if intent == "order":
            await self._propose_lines(turn, data, proposal.lines)
        elif intent == "menu":
            await self._show_menu(turn, data)
        elif intent == "status":
            await self._status(turn, data)
        elif intent == "handoff":
            self._handoff(turn, "requested")
        elif intent == "cancel":
            await self._cancel(turn, data)
        elif intent == "delivery_info":
            s = await data.settings()
            turn.say(
                copy.delivery_info(
                    delivery=s.delivery_enabled,
                    takeaway=s.takeaway_enabled,
                    fee_paisa=s.delivery_fee_paisa,
                    minimum_paisa=s.delivery_min_order_paisa,
                )
            )
        elif intent == "payment_claim":
            turn.say(copy.PAYMENT_CLAIM)
        else:
            self._miss(turn)

    async def _propose_lines(
        self, turn: _Turn, data: _Data, proposed: tuple[ProposedLine, ...]
    ) -> None:
        """Resolve each proposed line against the published menu and availability, and price
        it on the server. Unknown SKUs are dropped; only database names and prices are shown."""
        live = await data.live()
        if live is None:
            turn.say(copy.NO_MENU)
            return
        lines: list[CartLine] = []
        quoted: list[QuotedLine] = []
        finished: list[str] = []
        notes: list[str] = []
        for p in proposed:
            item = live.item(p.sku)
            if item is None:
                continue
            if not live.is_available(item.sku):
                finished.append(item.name)
                continue
            line = CartLine(item.sku, p.quantity, p.choices)
            try:
                quoted.append(price_line(live, line))
                lines.append(line)
            except QuoteRejected:
                # A fixed note with the database name only: the refusal text could repeat an
                # option name the model made up, and model text never reaches the customer.
                notes.append(f"{item.name}: please choose its options from the menu.")
        if finished:
            notes.insert(
                0,
                f"Sorry, {', '.join(finished)} {'is' if len(finished) == 1 else 'are'} "
                "finished for today 😔",
            )
        if not lines:
            if not notes:
                self._miss(turn)
                return
            turn.state = "browsing"
            turn.say(
                "\n".join([*notes, "Reply MENU to see what's available, or 0 to talk to staff."])
            )
            return
        turn.state = "proposal"
        turn.context["proposal"] = _store(lines)
        turn.say(render.proposal(tuple(quoted), notes))

    # Checkout (P6-T5) ----------------------------------------------------------------------

    async def _checkout(self, turn: _Turn, data: _Data) -> None:
        turn.leave_checkout()
        turn.state = "browsing"
        if not turn.cart():
            turn.say(copy.CART_EMPTY)
            return
        decision = await data.gate()
        if not decision.open:
            turn.say(copy.closed(await data.restaurant(), decision.reason, decision.reopens_at))
            return
        quoted, notes = await self._priced_cart(turn, data)
        if notes:
            # Something changed since it was added: show the cart again before going on.
            turn.say(render.cart(quoted, notes) if quoted else copy.CART_EMPTY)
            return
        settings = await data.settings()
        options: list[Fulfilment] = []
        if settings.delivery_enabled:
            options.append("delivery")
        if settings.takeaway_enabled:
            options.append("takeaway")
        if len(options) == 1:
            await self._set_fulfilment(turn, data, options[0])
            return
        turn.state = "fulfilment"
        turn.context["fulfilment_options"] = options
        delivery = f"Delivery ({format_rupees(settings.delivery_fee_paisa)} delivery fee" + (
            f", minimum order {format_rupees(settings.delivery_min_order_paisa)})"
            if settings.delivery_min_order_paisa
            else ")"
        )
        labels = {"delivery": delivery, "takeaway": "Takeaway"}
        turn.say(render.numbered(copy.FULFILMENT_ASK, [labels[o] for o in options]))

    async def _pick_fulfilment(self, turn: _Turn, data: _Data, words: str) -> None:
        options: list[Fulfilment] = turn.context.get("fulfilment_options") or []
        chosen: Fulfilment | None = None
        if words.isdigit() and 1 <= int(words) <= len(options):
            chosen = options[int(words) - 1]
        elif words in ("delivery", "takeaway", "pickup"):
            named: Fulfilment = "takeaway" if words == "pickup" else words  # type: ignore[assignment]
            chosen = named if named in options else None
        if chosen is None:
            turn.say(copy.CHOOSE_NUMBER)
            return
        await self._set_fulfilment(turn, data, chosen)

    async def _set_fulfilment(self, turn: _Turn, data: _Data, chosen: Fulfilment) -> None:
        settings = await data.settings()
        turn.context["fulfilment"] = chosen
        if chosen == "delivery":
            quoted, _ = await self._priced_cart(turn, data)
            subtotal = sum(q.line_total_paisa for q in quoted)
            if subtotal < settings.delivery_min_order_paisa:
                turn.state = "browsing"
                turn.leave_checkout()
                turn.say(
                    f"The minimum order for delivery is "
                    f"{format_rupees(settings.delivery_min_order_paisa)}, and your cart is "
                    f"{format_rupees(subtotal)}. Reply MENU to add more."
                )
                return
            turn.state = "address"
            turn.say(copy.ADDRESS_ASK)
            return
        earliest, latest = pickup_window(settings, turn.now)
        # The first slot is the earliest time rounded up to a whole 5 minutes.
        start = earliest.replace(second=0, microsecond=0)
        if start < earliest:
            start += timedelta(minutes=1)
        start += timedelta(minutes=(-start.minute) % 5)
        options: list[datetime] = []
        at = start
        while at <= latest and len(options) < PICKUP_OPTIONS:
            options.append(at)
            at += PICKUP_STEP
        turn.state = "pickup_time"
        turn.context["pickup_options"] = [o.isoformat() for o in options]
        labels = [format_time(o) for o in options]
        if labels:
            labels[0] = f"As soon as possible (about {labels[0]})"
        turn.say(render.numbered(copy.PICKUP_ASK, labels))

    async def _address(
        self, turn: _Turn, data: _Data, text: str | None, message_id: str | None
    ) -> None:
        if text is None:
            # The canonical event carries no coordinates yet (P5-T2 finding); staff see the
            # pin in the chat. The message id ties the order to it.
            turn.context["address"] = "📍 Shared location"
            turn.context["address_message"] = message_id
        else:
            address = " ".join(text.split())
            if len(address) < ADDRESS_MIN:
                turn.say(copy.ADDRESS_TOO_SHORT)
                return
            turn.context["address"] = address[:ADDRESS_MAX]
        turn.state = "delivery_note"
        turn.say(copy.NOTE_ASK)

    async def _pick_pickup(self, turn: _Turn, data: _Data, text: str, words: str) -> None:
        options: list[str] = turn.context.get("pickup_options") or []
        settings = await data.settings()
        at: datetime | None
        if words.isdigit() and 1 <= int(words) <= len(options):
            at = datetime.fromisoformat(options[int(words) - 1])
        else:
            at = parse_pickup(text, turn.now)
        if at is None:
            turn.say(copy.CHOOSE_NUMBER)
            return
        try:
            check_pickup_time(settings, turn.now, at)
        except QuoteRejected as exc:
            turn.say(f"Sorry, {exc}. {copy.PICKUP_ASK}")
            return
        turn.context["pickup_at"] = at.isoformat()
        await self._ask_payment(turn, data)

    async def _ask_payment(self, turn: _Turn, data: _Data) -> None:
        live = await data.live()
        settings = await data.settings()
        fulfilment: Fulfilment = turn.context["fulfilment"]
        if live is None:
            await self._show_menu(turn, data)
            return
        first_order = not await data.has_orders(turn.chat.customer_id)
        methods: list[PaymentMethod] = []
        # Why a method can't be offered: shown only if none can. The COD first-order limit
        # is worth telling the customer even when transfer remains (AD-03).
        refusals: list[str] = []
        cod_note: str | None = None
        method: PaymentMethod
        for method in ("cod", "transfer"):
            try:
                quote = price_cart(
                    live, settings, turn.cart(), fulfilment=fulfilment, payment_method=method
                )
            except QuoteRejected as exc:
                if exc.code != "payment_unavailable":
                    refusals.append(str(exc))
                continue
            if method == "cod" and not cod_allowed(
                settings, quote.total_paisa, first_order=first_order
            ):
                limit = settings.cod_first_order_limit_paisa or 0
                cod_note = (
                    f"cash on delivery is available for first orders up to {format_rupees(limit)}"
                )
                refusals.append(cod_note)
                continue
            methods.append(method)
        if not methods:
            turn.state = "browsing"
            turn.leave_checkout()
            reason = refusals[0] if refusals else "this restaurant can't take orders right now"
            turn.say(f"Sorry, {reason}. Reply CART to review your cart, or 0 to talk to staff.")
            return
        if len(methods) == 1:
            turn.context["payment_method"] = methods[0]
            if cod_note is not None:
                turn.say(f"Note: {cod_note}.")
            await self._summary(turn, data)
            return
        turn.state = "payment"
        turn.context["payment_options"] = methods
        turn.say(render.numbered(copy.PAYMENT_ASK, [copy.PAYMENT_LABELS[m] for m in methods]))

    async def _pick_payment(self, turn: _Turn, data: _Data, words: str) -> None:
        options: list[PaymentMethod] = turn.context.get("payment_options") or []
        chosen: PaymentMethod | None = None
        if words.isdigit() and 1 <= int(words) <= len(options):
            chosen = options[int(words) - 1]
        elif PAYMENT_WORDS.get(words) in options:
            chosen = PAYMENT_WORDS[words]
        if chosen is None:
            turn.say(copy.CHOOSE_NUMBER)
            return
        turn.context["payment_method"] = chosen
        await self._summary(turn, data)

    async def _summary(self, turn: _Turn, data: _Data) -> None:
        """The templated summary (A6): every line and amount from `price_cart`."""
        live = await data.live()
        settings = await data.settings()
        ctx = turn.context
        try:
            if live is None:
                raise QuoteRejected("no_menu", "the menu isn't available right now")
            quote = price_cart(
                live,
                settings,
                turn.cart(),
                fulfilment=ctx["fulfilment"],
                payment_method=ctx["payment_method"],
            )
            pickup_text = None
            if quote.fulfilment == "takeaway":
                pickup_at = datetime.fromisoformat(ctx["pickup_at"])
                check_pickup_time(settings, turn.now, pickup_at)
                pickup_text = format_time(pickup_at)
        except QuoteRejected as exc:
            turn.state = "browsing"
            turn.leave_checkout()
            turn.say(f"Sorry, {exc}. Reply CART to review your cart, or 0 to talk to staff.")
            return
        nonce = await placement.issue(
            data.conn,
            data.tenant,
            conversation_id=turn.chat.conversation_id,
            customer_id=turn.chat.customer_id,
            checkout=checkout_of(turn.context),
            quote=quote,
            now=turn.now,
            ttl=SUMMARY_TTL,
        )
        ctx["quote"] = {
            "nonce": nonce,
            "menu_version_id": str(quote.menu_version_id),
            "total_paisa": quote.total_paisa,
            "expires_at": (turn.now + SUMMARY_TTL).isoformat(),
        }
        turn.state = "summary"
        text = render.summary(
            quote,
            address=ctx.get("address"),
            delivery_note=ctx.get("delivery_note"),
            pickup_at_text=pickup_text,
        )
        turn.say(text)

    async def _confirm(self, turn: _Turn, data: _Data) -> None:
        """YES on the summary: place the order (P7-T2). The confirmation is queued by the order
        transaction itself. If the summary expired or anything changed, the customer gets the
        new summary to confirm instead (A7)."""
        chat = turn.chat
        try:
            # A savepoint: a refusal or a failure keeps nothing of the order.
            async with data.conn.transaction():
                await placement.place_order(
                    data.conn,
                    data.tenant,
                    (turn.context.get("quote") or {}).get("nonce"),
                    conversation_id=chat.conversation_id,
                    customer_id=chat.customer_id,
                    checkout=checkout_of(turn.context),
                    now=turn.now,
                )
        except placement.OrderRefused as exc:
            await self._refused(turn, data, exc)
            return
        except Exception as exc:
            log.error("placing an order failed, nothing was kept: %s", error_text(exc))
            turn.say(copy.ORDER_FAILED)
            return
        turn.state, turn.context = "idle", {}

    async def _refused(self, turn: _Turn, data: _Data, exc: placement.OrderRefused) -> None:
        if exc.code in ("nonce", "changed"):
            await self._summary(turn, data)
            if turn.state == "summary":
                turn.replies[-1] = f"{copy.SUMMARY_UPDATED}\n{turn.replies[-1]}"
            return
        turn.state = "browsing"
        turn.leave_checkout()
        if exc.code in ("closed", "at_capacity"):
            decision = exc.decision
            turn.say(
                copy.closed(
                    await data.restaurant(),
                    None if decision is None else decision.reason,
                    None if decision is None else decision.reopens_at,
                )
            )
        elif exc.code == "unavailable":
            # Re-confirmation: the cart without what can't be ordered, then CHECKOUT again.
            quoted, notes = await self._priced_cart(turn, data)
            turn.say(render.cart(quoted, notes) if quoted else "\n".join([*notes, copy.CART_EMPTY]))
        else:
            turn.say(f"Sorry, {exc}. Reply CART to review your cart, or 0 to talk to staff.")
