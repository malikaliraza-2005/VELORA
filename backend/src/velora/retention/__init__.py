"""Data retention (AD-38): customer data is kept only as long as the pilot needs it.

The worker sweeps every tenant once an hour (`Retention.sweep`), as `app_rw` inside the
tenant's scope, in batches:

- **Messages:** the text and the customer's number in the inbox and outbox (`payload`,
  `ordering_key`, `last_error`), 30 days after the message finished. They become
  `{"redacted": true}`; the row, its status and its times stay, for the counts.
- **Order confirmations** (`pending_actions.payload`: cart, quote, address): 30 days after
  they were used, voided or expired. Redacted.
- **Conversations** (`conversations.context`: cart and checkout details): after 30 days
  without activity, reset to `idle`, as if new.
- **Delivery address and note** on a finished order: removed 90 days after it finished. The
  order, its items and its amounts stay.
- **The customer** (number and name): anonymised (`anonymise_customer`) after 365 days without
  a conversation or an order.

Orders, items, payments, transitions and the audit log are kept: they are the business and
tax record. `payment_evidence` holds only the provider's media handle, not the media; the
media itself is removed by OpenWA's own retention (PC-7).

`forget` does all of this at once for one customer who asks to be forgotten. The founder runs
it (`python -m velora.retention forget`), and it is audited without the phone number.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from velora.clock import Clock
from velora.db import Database, TenantId
from velora.identity.audit import record

BATCH = 500
REDACTED = {"redacted": True}
INBOX_DONE = ("done", "failed", "ignored")
OUTBOX_DONE = ("sent", "delivered", "read", "failed", "unknown", "stale", "cancelled")
ORDER_DONE = ("completed", "cancelled", "rejected", "expired")
PHONE = re.compile(r"^\+[1-9][0-9]{6,14}$")


@dataclass(frozen=True)
class RetentionPolicy:
    messages: timedelta = timedelta(days=30)
    confirmations: timedelta = timedelta(days=30)
    conversations: timedelta = timedelta(days=30)
    delivery_addresses: timedelta = timedelta(days=90)
    customers: timedelta = timedelta(days=365)


@dataclass
class RetentionReport:
    inbox: int = 0
    outbox: int = 0
    confirmations: int = 0
    conversations: int = 0
    addresses: int = 0
    customers: int = 0

    @property
    def total(self) -> int:
        return sum(asdict(self).values())


class ForgetRefusedError(Exception):
    pass


async def _count(conn: AsyncConnection[Any], sql: str, params: dict[str, Any]) -> int:
    cur = await conn.execute(sql, params)
    return cur.rowcount if cur.rowcount > 0 else 0


# A conversation back to how a new one starts: idle, with nothing in its context.
RESET_STALE_CONVERSATIONS = (
    "WITH picked AS (SELECT id FROM velora.conversations "
    "WHERE GREATEST(updated_at, last_inbound_at, last_outbound_at) < %(conversations)s "
    "AND (state <> 'idle' OR context <> '{}'::jsonb) "
    "LIMIT %(limit)s FOR UPDATE SKIP LOCKED) "
    "UPDATE velora.conversations t SET state = 'idle', context = '{}'::jsonb, "
    "handoff_at = NULL, handoff_reason = NULL, state_version = t.state_version + 1, "
    "updated_at = %(now)s FROM picked WHERE t.id = picked.id"
)
RESET_CUSTOMER_CONVERSATIONS = (
    "UPDATE velora.conversations SET state = 'idle', context = '{}'::jsonb, "
    "handoff_at = NULL, handoff_reason = NULL, state_version = state_version + 1, "
    "updated_at = %(now)s WHERE customer_id = %(customer)s"
)


class Retention:
    """Each kind is picked first, in a CTE that runs once, then updated: an `id IN (SELECT ...
    LIMIT n)` subquery may be re-run by the planner and change more than `n` rows."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        policy: RetentionPolicy | None = None,
        *,
        batch: int = BATCH,
    ) -> None:
        self._db = db
        self._clock = clock
        self._policy = policy or RetentionPolicy()
        self._batch = batch

    async def sweep(self, tenant: TenantId) -> RetentionReport:
        """One batch of each kind for one tenant, in one transaction."""
        now = self._clock.now()
        p = self._policy
        params: dict[str, Any] = {
            "now": now,
            "redacted": Jsonb(REDACTED),
            "limit": self._batch,
            "inbox_done": list(INBOX_DONE),
            "outbox_done": list(OUTBOX_DONE),
            "order_done": list(ORDER_DONE),
            "messages": now - p.messages,
            "confirmations": now - p.confirmations,
            "conversations": now - p.conversations,
            "addresses": now - p.delivery_addresses,
            "customers": now - p.customers,
        }
        report = RetentionReport()
        async with self._db.tenant(tenant) as conn:
            report.inbox = await _count(
                conn,
                "WITH picked AS (SELECT id FROM velora.inbox_events "
                "WHERE status = ANY(%(inbox_done)s) AND received_at < %(messages)s "
                "AND payload <> %(redacted)s LIMIT %(limit)s FOR UPDATE SKIP LOCKED) "
                "UPDATE velora.inbox_events t SET payload = %(redacted)s, ordering_key = NULL, "
                "last_error = NULL FROM picked WHERE t.id = picked.id",
                params,
            )
            report.outbox = await _count(
                conn,
                "WITH picked AS (SELECT id FROM velora.outbox_messages "
                "WHERE status = ANY(%(outbox_done)s) AND created_at < %(messages)s "
                "AND payload <> %(redacted)s LIMIT %(limit)s FOR UPDATE SKIP LOCKED) "
                "UPDATE velora.outbox_messages t SET payload = %(redacted)s, ordering_key = NULL, "
                "last_error = NULL FROM picked WHERE t.id = picked.id",
                params,
            )
            report.confirmations = await _count(
                conn,
                "WITH picked AS (SELECT id FROM velora.pending_actions "
                "WHERE GREATEST(expires_at, consumed_at, voided_at) < %(confirmations)s "
                "AND payload <> %(redacted)s LIMIT %(limit)s FOR UPDATE SKIP LOCKED) "
                "UPDATE velora.pending_actions t SET payload = %(redacted)s "
                "FROM picked WHERE t.id = picked.id",
                params,
            )
            report.conversations = await _count(conn, RESET_STALE_CONVERSATIONS, params)
            report.addresses = await _count(
                conn,
                "WITH picked AS (SELECT id FROM velora.orders "
                "WHERE status = ANY(%(order_done)s) AND updated_at < %(addresses)s "
                "AND (delivery_address IS NOT NULL OR delivery_note IS NOT NULL) "
                "LIMIT %(limit)s FOR UPDATE SKIP LOCKED) "
                "UPDATE velora.orders t SET delivery_address = NULL, delivery_note = NULL, "
                "updated_at = %(now)s FROM picked WHERE t.id = picked.id",
                params,
            )
            cur = await conn.execute(
                """
                SELECT c.id FROM velora.customers c
                 WHERE c.anonymised_at IS NULL AND c.created_at < %(customers)s
                   AND NOT EXISTS (
                       SELECT 1 FROM velora.conversations v WHERE v.customer_id = c.id
                          AND GREATEST(v.created_at, v.last_inbound_at) >= %(customers)s)
                   AND NOT EXISTS (
                       SELECT 1 FROM velora.orders o WHERE o.customer_id = c.id
                          AND (o.created_at >= %(customers)s
                               OR NOT o.status = ANY(%(order_done)s)))
                 LIMIT %(limit)s FOR UPDATE OF c SKIP LOCKED
                """,
                params,
            )
            for (customer_id,) in await cur.fetchall():
                await conn.execute(
                    RESET_CUSTOMER_CONVERSATIONS, {**params, "customer": customer_id}
                )
                await conn.execute("SELECT velora.anonymise_customer(%s)", (customer_id,))
                report.customers += 1
        return report

    async def forget(self, tenant: TenantId, phone: str, *, operator: str) -> RetentionReport:
        """Remove one customer's personal data now, on their request. Refused while they have
        an order in progress; returns what was removed."""
        if not PHONE.match(phone):
            raise ForgetRefusedError("the phone number must be in +92... (E.164) form")
        now = self._clock.now()
        report = RetentionReport()
        async with self._db.tenant(tenant) as conn:
            cur = await conn.execute(
                "SELECT id FROM velora.customers WHERE phone_e164 = %s AND anonymised_at IS NULL "
                "FOR UPDATE",
                (phone,),
            )
            row = await cur.fetchone()
            if row is None:
                raise ForgetRefusedError("no customer with that number at this restaurant")
            customer_id: uuid.UUID = row[0]
            cur = await conn.execute(
                "SELECT count(*) FROM velora.orders WHERE customer_id = %s "
                "AND status NOT IN ('draft', 'awaiting_confirmation') "
                "AND NOT status = ANY(%s)",
                (customer_id, list(ORDER_DONE)),
            )
            open_orders = await cur.fetchone()
            if open_orders is not None and open_orders[0]:
                raise ForgetRefusedError(
                    "the customer has an order in progress: finish or cancel it first"
                )
            params: dict[str, Any] = {
                "now": now,
                "phone": phone,
                "customer": customer_id,
                "redacted": Jsonb(REDACTED),
            }
            # Queued replies to them are not sent.
            await conn.execute(
                "UPDATE velora.outbox_messages SET status = 'cancelled' WHERE ordering_key = "
                "%(phone)s AND status IN ('pending', 'waiting_for_channel')",
                params,
            )
            report.inbox = await _count(
                conn,
                "UPDATE velora.inbox_events SET payload = %(redacted)s, ordering_key = NULL, "
                "last_error = NULL WHERE ordering_key = %(phone)s AND status <> 'processing'",
                params,
            )
            report.outbox = await _count(
                conn,
                "UPDATE velora.outbox_messages SET payload = %(redacted)s, ordering_key = NULL, "
                "last_error = NULL WHERE ordering_key = %(phone)s AND status <> 'sending'",
                params,
            )
            # An open confirmation is voided first, so a later YES can't reach its redacted
            # payload.
            report.confirmations = await _count(
                conn,
                "UPDATE velora.pending_actions SET payload = %(redacted)s, "
                "voided_at = CASE WHEN consumed_at IS NULL AND voided_at IS NULL "
                "THEN %(now)s ELSE voided_at END, "
                "void_reason = CASE WHEN consumed_at IS NULL AND voided_at IS NULL "
                "THEN 'superseded' ELSE void_reason END "
                "WHERE customer_id = %(customer)s",
                params,
            )
            report.conversations = await _count(conn, RESET_CUSTOMER_CONVERSATIONS, params)
            await conn.execute("SELECT velora.anonymise_customer(%s)", (customer_id,))
            report.customers = 1
            await record(
                conn,
                tenant,
                at=now,
                actor_kind="platform",
                actor_id=None,
                action="customer.forgotten",
                target_type="customer",
                target_id=customer_id,
                details={"operator": operator, **asdict(report)},
            )
        return report


def due(last: datetime | None, now: datetime, interval: timedelta) -> bool:
    return last is None or now - last >= interval
