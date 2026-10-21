"""Customer status messages for orders, through the outbox (P7-T6, AD-20).

Each message is queued in the same transaction as the change it announces, with
`purpose="status_update"` (never dropped as stale). Nothing here waits for a send: the order's
state is already written, and a failed or unknown send changes nothing about it. The
customer can always ask with "status".

**Opted-out customers get no status messages** (AD-31): STOP means no more messages from the
assistant, and an order update is still a message from it. The order itself is unaffected.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from velora.conversation import copy
from velora.db import TenantId
from velora.identity.errors import expect_row
from velora.messaging.outbox import OutboundText, enqueue


async def restaurant_name(conn: AsyncConnection[Any]) -> str:
    cur = await conn.execute("SELECT display_name FROM velora.tenants")
    (name,) = expect_row(await cur.fetchone())
    return str(name)


async def send_to_customer(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    conversation_id: uuid.UUID | None,
    text: str,
    *,
    key: str,
    now: datetime,
    critical: bool = False,
) -> uuid.UUID | None:
    """Queue `text` for the order's chat. None if there is no one to send it to: no chat, an
    anonymised customer, or one who opted out."""
    if conversation_id is None:
        return None
    cur = await conn.execute(
        "SELECT c.channel_id, cu.phone_e164, cu.opted_out_at FROM velora.conversations c "
        "JOIN velora.customers cu ON cu.id = c.customer_id WHERE c.id = %s",
        (conversation_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    channel_id, phone, opted_out_at = row
    if phone is None or opted_out_at is not None:
        return None
    return await enqueue(
        conn,
        tenant,
        channel_id=channel_id,
        message=OutboundText(phone, text),
        idempotency_key=key,
        purpose="status_update",
        at=now,
        critical=critical,
        conversation_id=conversation_id,
    )


async def status_changed(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    *,
    reference: str,
    conversation_id: uuid.UUID | None,
    to: str,
    by_customer: bool,
    reason: str | None,
    now: datetime,
    key: str,
) -> uuid.UUID | None:
    text = copy.order_update(
        reference, to, await restaurant_name(conn), reason=reason, by_customer=by_customer
    )
    if text is None:
        return None
    return await send_to_customer(conn, tenant, conversation_id, text, key=key, now=now)
