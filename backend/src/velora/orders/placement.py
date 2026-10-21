"""The YES confirmation nonce and the order-creation transaction (P7-T1, T2, T5; AD-20).

**The nonce (`issue`).** Showing the templated summary issues a `pending_actions` row: a
random nonce (only its SHA-256 is stored), bound by RLS to the tenant and by columns to the
conversation, the customer and a hash of the exact checkout (`cart_hash`), and expiring with
the summary. A new summary voids the previous nonce, so at most one is open per chat.

**Placing the order (`place_order`).** The customer's YES runs these steps in the caller's
transaction; the engine wraps them in a savepoint, so if any step fails nothing is kept and the
customer gets a clear message:
1. the nonce must be open, unexpired and bound to this conversation, customer and checkout;
2. re-validate: the gate (global pause, restaurant status, channel, pause, menu, capacity);
3. re-price with `price_cart` and compare with what the summary showed (menu version, every
   line, every amount). Any difference refuses with `changed`, and the customer confirms the
   new summary;
4. consume the nonce (`UPDATE … WHERE consumed_at IS NULL …`, single use);
5. create the order from the nonce's snapshot: claim a capacity place, take the next order
   number, write the order, its lines and its first transition;
6. queue the confirmation in the outbox (critical, a status update).

Two YES replies can't create two orders: the conversation lock serialises them, the nonce
update lets only one through, and `orders.pending_action_id` is unique.

**Cash on delivery (P8-T2, AD-32).** A blocklisted customer can't pay cash, and a customer
with no completed order yet is on their first order, whose total the restaurant may limit.
Both are checked when the payment method is offered and again here, at YES.

**Possible duplicates (P7-T5).** An order with the same customer and cart hash as one placed
in the last 10 minutes is flagged `possible_duplicate` for staff. It is never cancelled
automatically.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from velora.conversation import copy
from velora.db import TenantId
from velora.identity import audit
from velora.identity.errors import expect_row
from velora.menu.store import live_menu
from velora.ordering import gate
from velora.ordering.pricing import (
    CartLine,
    Fulfilment,
    PaymentMethod,
    Quote,
    QuoteRejected,
    cod_allowed,
    pickup_window,
    price_cart,
    transfer_deadline,
)
from velora.ordering.settings import OrderingSettings, load_settings
from velora.orders import notify
from velora.orders.lifecycle import INITIAL_STATUS, Status, order_reference

DUPLICATE_WINDOW = timedelta(minutes=10)


@dataclass(frozen=True)
class Checkout:
    """What the customer is confirming: the cart and every checkout choice. No amounts."""

    lines: tuple[CartLine, ...]
    fulfilment: Fulfilment
    payment_method: PaymentMethod
    address: str | None = None
    address_message: str | None = None
    delivery_note: str | None = None
    pickup_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "lines": [
                {"sku": ln.sku, "quantity": ln.quantity, "choices": [list(c) for c in ln.choices]}
                for ln in self.lines
            ],
            "fulfilment": self.fulfilment,
            "payment_method": self.payment_method,
            "address": self.address,
            "address_message": self.address_message,
            "delivery_note": self.delivery_note,
            "pickup_at": None if self.pickup_at is None else self.pickup_at.isoformat(),
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Checkout:
        return cls(
            lines=tuple(
                CartLine(
                    str(r["sku"]),
                    int(r["quantity"]),
                    tuple((str(g), str(o)) for g, o in r["choices"]),
                )
                for r in raw["lines"]
            ),
            fulfilment=raw["fulfilment"],
            payment_method=raw["payment_method"],
            address=raw.get("address"),
            address_message=raw.get("address_message"),
            delivery_note=raw.get("delivery_note"),
            pickup_at=None
            if raw.get("pickup_at") is None
            else datetime.fromisoformat(raw["pickup_at"]),
        )


def cart_hash(checkout: Checkout) -> bytes:
    """SHA-256 of the checkout in a canonical form. Option names compare case-insensitively,
    as pricing does."""
    canonical = checkout.to_json()
    for line in canonical["lines"]:
        line["sku"] = line["sku"].upper()
        line["choices"] = sorted([g.casefold(), o.casefold()] for g, o in line["choices"])
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).digest()


def _nonce_hash(nonce: str) -> bytes:
    return hashlib.sha256(nonce.encode()).digest()


def _quote_snapshot(quote: Quote) -> dict[str, Any]:
    return {
        "menu_version_id": str(quote.menu_version_id),
        "lines": [
            {
                "sku": q.sku,
                "name": q.name,
                "choices": [list(c) for c in q.choices],
                "unit_price_paisa": q.unit_price_paisa,
                "quantity": q.quantity,
                "line_total_paisa": q.line_total_paisa,
            }
            for q in quote.lines
        ],
        "subtotal_paisa": quote.subtotal_paisa,
        "delivery_fee_paisa": quote.delivery_fee_paisa,
        "tax_bps": quote.tax_bps,
        "tax_paisa": quote.tax_paisa,
        "total_paisa": quote.total_paisa,
    }


class OrderRefused(Exception):  # noqa: N818 - a refusal, not a fault
    """The order can't be placed as confirmed. Nothing was kept.

    Codes: `nonce` (missing, expired, used, voided or bound elsewhere), `changed` (the price or
    menu differs from the summary), `closed` (the gate), `unavailable` (a line can't be priced
    now), `at_capacity`, `cod_limit`, `pickup_time` and other pricing refusals. The message is
    safe to show the customer.
    """

    def __init__(self, code: str, message: str, *, decision: gate.GateDecision | None = None):
        super().__init__(message)
        self.code = code
        self.decision = decision


@dataclass(frozen=True)
class PendingConfirmation:
    id: uuid.UUID
    conversation_id: uuid.UUID
    customer_id: uuid.UUID
    cart_hash: bytes
    created_at: datetime
    expires_at: datetime
    checkout: Checkout
    quote: dict[str, Any]


@dataclass(frozen=True)
class Placed:
    order_id: uuid.UUID
    reference: str
    status: Status
    total_paisa: int
    payment_due_at: datetime | None
    possible_duplicate: bool


# The nonce (P7-T1) -------------------------------------------------------------------------


async def issue(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    *,
    conversation_id: uuid.UUID,
    customer_id: uuid.UUID,
    checkout: Checkout,
    quote: Quote,
    now: datetime,
    ttl: timedelta,
) -> str:
    """Issue the nonce for a summary just shown. Voids the chat's previous one."""
    await conn.execute(
        "UPDATE velora.pending_actions SET voided_at = %s, void_reason = 'superseded' "
        "WHERE conversation_id = %s AND consumed_at IS NULL AND voided_at IS NULL",
        (now, conversation_id),
    )
    nonce = secrets.token_urlsafe(24)
    await conn.execute(
        "INSERT INTO velora.pending_actions (tenant_id, conversation_id, customer_id, kind, "
        "nonce_hash, cart_hash, payload, created_at, expires_at) "
        "VALUES (%s, %s, %s, 'confirm_order', %s, %s, %s, %s, %s)",
        (
            tenant.value,
            conversation_id,
            customer_id,
            _nonce_hash(nonce),
            cart_hash(checkout),
            Jsonb({"checkout": checkout.to_json(), "quote": _quote_snapshot(quote)}),
            now,
            now + ttl,
        ),
    )
    return nonce


async def claimable(
    conn: AsyncConnection[Any],
    nonce: str | None,
    *,
    conversation_id: uuid.UUID,
    customer_id: uuid.UUID,
    checkout: Checkout,
    now: datetime,
) -> PendingConfirmation:
    """The open confirmation for this nonce, locked. Refuses (`nonce`) if it is missing,
    expired, used or voided, or bound to another conversation, customer or checkout."""
    if not nonce:
        raise OrderRefused("nonce", "this order summary is no longer valid")
    cur = await conn.execute(
        "SELECT id, conversation_id, customer_id, cart_hash, created_at, expires_at, "
        "consumed_at, voided_at, payload FROM velora.pending_actions "
        "WHERE nonce_hash = %s AND kind = 'confirm_order' FOR UPDATE",
        (_nonce_hash(nonce),),
    )
    row = await cur.fetchone()
    if row is None:
        raise OrderRefused("nonce", "this order summary is no longer valid")
    action_id, conv, customer, bound_hash, created_at, expires_at, consumed, voided, payload = row
    if (conv, customer) != (conversation_id, customer_id) or bytes(bound_hash) != cart_hash(
        checkout
    ):
        raise OrderRefused("nonce", "this order summary doesn't match your cart")
    if consumed is not None or voided is not None or now >= expires_at:
        raise OrderRefused("nonce", "this order summary has expired")
    return PendingConfirmation(
        action_id,
        conv,
        customer,
        bytes(bound_hash),
        created_at,
        expires_at,
        Checkout.from_json(payload["checkout"]),
        payload["quote"],
    )


# The order transaction (P7-T2) -----------------------------------------------------------


@dataclass(frozen=True)
class CodStanding:
    # No completed order yet: earlier cancelled, rejected or expired orders don't count.
    first_order: bool
    blocked: bool


async def cod_standing(conn: AsyncConnection[Any], customer_id: uuid.UUID) -> CodStanding:
    cur = await conn.execute(
        "SELECT cu.cod_blocked_at IS NOT NULL, NOT EXISTS (SELECT 1 FROM velora.orders o "
        "WHERE o.customer_id = cu.id AND o.status = 'completed') "
        "FROM velora.customers cu WHERE cu.id = %s",
        (customer_id,),
    )
    blocked, first_order = expect_row(await cur.fetchone())
    return CodStanding(first_order=first_order, blocked=blocked)


async def revalidate(
    conn: AsyncConnection[Any], tenant: TenantId, now: datetime
) -> OrderingSettings:
    """Step 2: whether the restaurant takes this order right now."""
    decision = await gate.check_new_cart(conn, tenant, now)
    if not decision.open:
        raise OrderRefused("closed", "the restaurant isn't taking orders now", decision=decision)
    return await load_settings(conn, tenant)


async def reprice(
    conn: AsyncConnection[Any],
    settings: OrderingSettings,
    pending: PendingConfirmation,
    *,
    standing: CodStanding,
    now: datetime,
) -> Quote:
    """Step 3: price the confirmed checkout again and compare with the summary."""
    live = await live_menu(conn)
    checkout = pending.checkout
    if live is None:
        raise OrderRefused("closed", "the menu isn't available right now")
    try:
        quote = price_cart(
            live,
            settings,
            list(checkout.lines),
            fulfilment=checkout.fulfilment,
            payment_method=checkout.payment_method,
        )
    except QuoteRejected as exc:
        code = "unavailable" if exc.code in ("unavailable", "unknown_item") else exc.code
        raise OrderRefused(code, str(exc)) from exc
    if _quote_snapshot(quote) != pending.quote:
        raise OrderRefused("changed", "the price or menu changed since the summary")
    if checkout.fulfilment == "takeaway":
        # The lead time is counted from when the customer saw the summary, so a slot that
        # was valid then is still accepted; it must still be in the future.
        earliest, latest = pickup_window(settings, pending.created_at)
        at = checkout.pickup_at
        if at is None or not (earliest <= at <= latest) or at <= now:
            raise OrderRefused("pickup_time", "that pickup time has passed")
    if checkout.payment_method == "cod" and standing.blocked:
        raise OrderRefused("cod_blocked", copy.COD_BLOCKED)
    if checkout.payment_method == "cod" and not cod_allowed(
        settings, quote.total_paisa, first_order=standing.first_order
    ):
        raise OrderRefused("cod_limit", "cash on delivery isn't available for this order")
    return quote


async def consume(conn: AsyncConnection[Any], pending: PendingConfirmation, now: datetime) -> None:
    """Step 4: use the nonce. Exactly one caller can."""
    cur = await conn.execute(
        "UPDATE velora.pending_actions SET consumed_at = %s WHERE id = %s "
        "AND consumed_at IS NULL AND voided_at IS NULL AND expires_at > %s",
        (now, pending.id, now),
    )
    if cur.rowcount != 1:
        raise OrderRefused("nonce", "this order summary has already been used")


async def create(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    settings: OrderingSettings,
    pending: PendingConfirmation,
    quote: Quote,
    now: datetime,
) -> Placed:
    """Step 5: the order, its lines and its first transition, from the snapshot."""
    if not await gate.claim_capacity(conn, tenant, settings, now):
        raise OrderRefused(
            "at_capacity",
            "the restaurant is very busy right now",
            decision=gate.GateDecision(False, "at_capacity", gate.slot_start(now) + gate.SLOT),
        )
    checkout = pending.checkout
    status = INITIAL_STATUS[checkout.payment_method]
    due = transfer_deadline(settings, now) if checkout.payment_method == "transfer" else None
    cur = await conn.execute(
        "SELECT 1 FROM velora.orders WHERE customer_id = %s AND cart_hash = %s "
        "AND created_at > %s LIMIT 1",
        (pending.customer_id, pending.cart_hash, now - DUPLICATE_WINDOW),
    )
    duplicate = await cur.fetchone() is not None
    cur = await conn.execute(
        "INSERT INTO velora.orders (tenant_id, order_number, customer_id, conversation_id, "
        "pending_action_id, status, fulfilment, payment_method, subtotal_paisa, "
        "delivery_fee_paisa, tax_paisa, total_paisa, delivery_address, delivery_note, "
        "pickup_at, possible_duplicate, menu_version_id, tax_bps, cart_hash, capacity_slot, "
        "payment_due_at, created_at, updated_at) VALUES (%s, velora.next_order_number(), %s, %s, "
        "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, order_number",
        (
            tenant.value,
            pending.customer_id,
            pending.conversation_id,
            pending.id,
            status,
            checkout.fulfilment,
            checkout.payment_method,
            quote.subtotal_paisa,
            quote.delivery_fee_paisa,
            quote.tax_paisa,
            quote.total_paisa,
            checkout.address if checkout.fulfilment == "delivery" else None,
            checkout.delivery_note if checkout.fulfilment == "delivery" else None,
            checkout.pickup_at,
            duplicate,
            quote.menu_version_id,
            quote.tax_bps,
            pending.cart_hash,
            gate.slot_start(now),
            due,
            now,
            now,
        ),
    )
    order_id, number = expect_row(await cur.fetchone())
    for line_no, line in enumerate(quote.lines, 1):
        await conn.execute(
            "INSERT INTO velora.order_items (tenant_id, order_id, line_no, sku, name_snapshot, "
            "unit_price_paisa, quantity, line_total_paisa, choices) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                tenant.value,
                order_id,
                line_no,
                line.sku,
                line.name,
                line.unit_price_paisa,
                line.quantity,
                line.line_total_paisa,
                Jsonb([list(c) for c in line.choices]),
            ),
        )
    await conn.execute(
        "INSERT INTO velora.order_transitions (tenant_id, order_id, from_status, to_status, "
        "actor_kind, created_at) VALUES (%s, %s, NULL, %s, 'customer', %s)",
        (tenant.value, order_id, status, now),
    )
    await audit.record(
        conn,
        tenant,
        at=now,
        actor_kind="customer",
        actor_id=pending.customer_id,
        action="order.placed",
        target_type="order",
        target_id=order_id,
        details={"status": status, "total_paisa": quote.total_paisa, "duplicate": duplicate},
    )
    cur = await conn.execute("SELECT order_number_prefix FROM velora.tenants")
    (prefix,) = expect_row(await cur.fetchone())
    return Placed(
        order_id, order_reference(prefix, number), status, quote.total_paisa, due, duplicate
    )


async def announce(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    pending: PendingConfirmation,
    placed: Placed,
    now: datetime,
    *,
    transfer_details: str | None,
) -> None:
    """Step 6: the confirmation (A7), queued with the order. Its delivery changes nothing. A
    transfer order's confirmation carries the account details (P8-T3)."""
    await notify.send_to_customer(
        conn,
        tenant,
        pending.conversation_id,
        copy.order_placed(
            placed.reference,
            total_paisa=placed.total_paisa,
            payment_method=pending.checkout.payment_method,
            payment_due_at=placed.payment_due_at,
            transfer_details=transfer_details,
        ),
        key=f"order-placed:{placed.order_id}",
        now=now,
        critical=True,
    )


async def place_order(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    nonce: str | None,
    *,
    conversation_id: uuid.UUID,
    customer_id: uuid.UUID,
    checkout: Checkout,
    now: datetime,
) -> Placed:
    """Place the confirmed order in the caller's transaction (see the module notes). Raises
    `OrderRefused`; the caller must roll back to before the call on any exception."""
    pending = await claimable(
        conn,
        nonce,
        conversation_id=conversation_id,
        customer_id=customer_id,
        checkout=checkout,
        now=now,
    )
    settings = await revalidate(conn, tenant, now)
    standing = await cod_standing(conn, customer_id)
    quote = await reprice(conn, settings, pending, standing=standing, now=now)
    await consume(conn, pending, now)
    placed = await create(conn, tenant, settings, pending, quote, now)
    await announce(
        conn, tenant, pending, placed, now, transfer_details=settings.transfer_account_details
    )
    return placed
