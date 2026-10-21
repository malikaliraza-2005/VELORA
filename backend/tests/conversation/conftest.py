"""Fixtures for the P6 conversation tests.

Each test gets its own restaurant (`new_live_shop`) with the menu below published as version
2 and placeholder tax rates set (B3). A `Customer` drives one chat end to end: the message
goes through the FakeProvider's signed webhook, `Ingress`, the inbox and the conversation
engine, and the replies are read back from the outbox in the order they were queued.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest

from tests.db.conftest import PgDatabase
from tests.messaging.conftest import (  # noqa: F401 - fixtures
    T0,
    Env,
    Shop,
    clock,
    cluster,
    db,
    fake,
    new_live_shop,
    rows,
    run,
    tenants,
)
from velora.ai.proposer import Proposal, ProposalRequest, ProposerError
from velora.conversation.engine import ConversationEngine
from velora.messaging.inbox import InboxProcessor, Ingress
from velora.messaging.outbox import apply_delivery_status
from velora.messaging.provider import InboundMedia, InboundMessage, MessageKind

# (category, [(sku, name, price, [(group, min, max, [(option, delta)])])])
MENU: list[
    tuple[str, list[tuple[str, str, int, list[tuple[str, int, int, list[tuple[str, int]]]]]]]
] = [
    (
        "Burgers",
        [
            (
                "ZINGER",
                "Zinger Burger",
                65000,
                [("Size", 1, 1, [("Regular", 0), ("Large", 15000)])],
            ),
            ("BEEF", "Beef Smash Burger", 89000, []),
        ],
    ),
    (
        "Drinks",
        [
            ("COKE", "Coca-Cola 345ml", 12000, []),
            ("SPRITE", "Sprite 345ml", 12000, []),
        ],
    ),
]
PRICES = {"ZINGER": 65000, "BEEF": 89000, "COKE": 12000, "SPRITE": 12000}
LARGE = 15000
TAX_COD_BPS, TAX_TRANSFER_BPS = 1600, 500
TRANSFER_DETAILS = "Meezan Bank — Test Kitchen — PK00MEZN0000000000000001"
DELIVERY_FEE, DELIVERY_MIN = 15000, 80000

_ids = itertools.count(1)


def publish_menu(pg: PgDatabase, shop: Shop, menu: list[Any] = MENU, version_no: int = 2) -> None:
    """Publish `menu` as the shop's live version (as the administrator)."""
    t = shop.tenant.value
    with pg.admin() as conn, conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t),))
        row = conn.execute(
            "SELECT id FROM velora.staff_members WHERE tenant_id = %s LIMIT 1", (t,)
        ).fetchone()
        assert row is not None
        staff = row[0]
        version = conn.execute(
            "INSERT INTO velora.menu_versions (tenant_id, version_no, created_by_kind, "
            "created_by_staff_id) VALUES (%s, %s, 'staff', %s) RETURNING id",
            (t, version_no, staff),
        ).fetchone()
        assert version is not None
        for cpos, (cname, items) in enumerate(menu):
            category = conn.execute(
                "INSERT INTO velora.menu_categories (tenant_id, menu_version_id, position, name) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (t, version[0], cpos, cname),
            ).fetchone()
            assert category is not None
            for ipos, (sku, name, price, groups) in enumerate(items):
                item = conn.execute(
                    "INSERT INTO velora.menu_items (tenant_id, menu_version_id, category_id, "
                    "position, sku, name, price_paisa) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id",
                    (t, version[0], category[0], ipos, sku, name, price),
                ).fetchone()
                assert item is not None
                for gpos, (gname, low, high, options) in enumerate(groups):
                    group = conn.execute(
                        "INSERT INTO velora.modifier_groups (tenant_id, menu_version_id, item_id, "
                        "position, name, min_select, max_select) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (t, version[0], item[0], gpos, gname, low, high),
                    ).fetchone()
                    assert group is not None
                    for opos, (oname, delta) in enumerate(options):
                        conn.execute(
                            "INSERT INTO velora.modifier_options (tenant_id, menu_version_id, "
                            "group_id, position, name, price_delta_paisa) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (t, version[0], group[0], opos, oname, delta),
                        )
        conn.execute(
            "UPDATE velora.menu_versions SET frozen_at = now() WHERE id = %s", (version[0],)
        )
        conn.execute(
            "INSERT INTO velora.menu_publications (tenant_id, publication_no, menu_version_id, "
            "published_by_staff_id) VALUES (%s, %s, %s, %s)",
            (t, version_no, version[0], staff),
        )


def set_settings(pg: PgDatabase, shop: Shop, **values: Any) -> None:
    with pg.admin() as conn:
        assignments = ", ".join(f"{k} = %({k})s" for k in values)
        conn.execute(
            f"UPDATE velora.ordering_settings SET {assignments} WHERE tenant_id = %(t)s".encode(),  # noqa: S608
            {**values, "t": shop.tenant.value},
        )


def set_available(pg: PgDatabase, shop: Shop, sku: str, available: bool) -> None:
    with pg.admin() as conn:
        conn.execute(
            "INSERT INTO velora.menu_item_availability (tenant_id, sku, is_available, "
            "updated_by_staff_id) SELECT %s, %s, %s, id FROM velora.staff_members "
            "WHERE tenant_id = %s LIMIT 1 ON CONFLICT (tenant_id, sku) "
            "DO UPDATE SET is_available = EXCLUDED.is_available",
            (shop.tenant.value, sku, available, shop.tenant.value),
        )


def restaurant_shop(pg: PgDatabase) -> Shop:
    shop = new_live_shop(pg)
    publish_menu(pg, shop)
    set_settings(
        pg,
        shop,
        tax_cod_bps=TAX_COD_BPS,
        tax_transfer_bps=TAX_TRANSFER_BPS,
        delivery_fee_paisa=DELIVERY_FEE,
        delivery_min_order_paisa=DELIVERY_MIN,
        transfer_account_details=TRANSFER_DETAILS,
    )
    return shop


@pytest.fixture()
def shop(db: PgDatabase) -> Shop:  # noqa: F811
    return restaurant_shop(db)


class FakeProposer:
    """A stand-in model: answers from a table, or by calling a function. Records requests."""

    def __init__(
        self,
        answer: Proposal | Callable[[ProposalRequest], Awaitable[Proposal]] | None = None,
    ) -> None:
        self.answer = answer
        self.requests: list[ProposalRequest] = []

    async def propose(self, request: ProposalRequest) -> Proposal:
        self.requests.append(request)
        if self.answer is None:
            raise ProposerError("no answer configured")
        if isinstance(self.answer, Proposal):
            return self.answer
        return await self.answer(request)


@dataclass
class Customer:
    env: Env
    pg: PgDatabase
    shop: Shop
    engine: ConversationEngine
    phone: str = field(default_factory=lambda: f"+92300{next(_ids) + 5_000_000:07d}")
    seen: set[uuid.UUID] = field(default_factory=set)

    async def deliver(
        self, text: str | None, kind: MessageKind = "text", media: InboundMedia | None = None
    ) -> None:
        key = f"msg-{uuid.uuid4().hex}"
        event = InboundMessage(
            session_ref=self.shop.session_ref,
            dedup_key=key,
            provider_message_id=key,
            sender=self.phone,
            chat_kind="direct",
            kind=kind,
            text=text,
            received_at=self.env.clock.now(),
            media=media,
        )
        headers, body = self.env.fake.signed_webhook(self.shop.session_ref, [event])
        parsed = self.env.fake.parse_webhook(self.shop.session_ref, headers, body)
        await Ingress(self.env.db, self.env.clock).receive("fake", self.shop.session_ref, parsed)

    def processor(self) -> InboxProcessor:
        return InboxProcessor(
            self.env.db,
            self.env.clock,
            {"message": self.engine.handle, "delivery_status": apply_delivery_status},
        )

    async def send(
        self, text: str | None, kind: MessageKind = "text", media: InboundMedia | None = None
    ) -> list[str]:
        """Send one message, let a worker process it, and return the replies it queued."""
        await self.deliver(text, kind, media)
        await self.processor().run_once(self.shop.tenant)
        self.env.clock.advance(timedelta(seconds=5))
        return self.new_replies()

    def _queued(self) -> list[tuple[uuid.UUID, str]]:
        found = rows(
            self.pg,
            "SELECT id, payload->>'text' FROM velora.outbox_messages WHERE tenant_id = %s "
            "AND ordering_key = %s AND status <> 'cancelled' ORDER BY seq",
            self.shop.tenant.value,
            self.phone,
        )
        return [(r[0], r[1]) for r in found]

    def all_replies(self) -> list[str]:
        """Every reply queued for this customer and not cancelled, in order."""
        return [text for _, text in self._queued()]

    def new_replies(self) -> list[str]:
        """Replies queued since the last call (cancelled ones excluded)."""
        fresh = [(i, text) for i, text in self._queued() if i not in self.seen]
        self.seen.update(i for i, _ in fresh)
        return [text for _, text in fresh]

    def conversation(self) -> tuple[Any, ...]:
        found = rows(
            self.pg,
            "SELECT c.state, c.context, c.handoff_reason, cu.opted_out_at "
            "FROM velora.conversations c "
            "JOIN velora.customers cu ON cu.id = c.customer_id "
            "WHERE c.tenant_id = %s AND cu.phone_e164 = %s",
            self.shop.tenant.value,
            self.phone,
        )
        assert len(found) == 1
        return found[0]


def engine_for(env: Env, proposer: Any = None, *, ai_timeout: float = 2.0) -> ConversationEngine:
    return ConversationEngine(env.clock, env.providers, proposer, ai_timeout=ai_timeout)


def chat(env: Env, pg: PgDatabase, shop: Shop, proposer: Any = None, **kwargs: Any) -> Customer:
    return Customer(env, pg, shop, engine_for(env, proposer, **kwargs))
