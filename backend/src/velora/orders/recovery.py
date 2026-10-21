"""The P7 part of recovery mode (P7-T7, AD-21; step 4 of the P11 recovery list).

After an outage, a YES nonce issued before it began must not be honoured: prices, availability
or the customer's intent may have changed while nothing was answered. `expire_pre_outage`
voids every open nonce created before `outage_started_at`. Each customer whose summary was
still valid when the outage began is asked to review the cart and confirm again (A13). A later
YES on the old summary is refused (`nonce`), and the customer gets a new summary.

P11 runs this from its recovery mode on startup; the rest of that mode is P11's.
"""

from __future__ import annotations

from datetime import datetime

from velora.conversation import copy
from velora.db import Database, TenantId
from velora.orders.notify import send_to_customer


async def expire_pre_outage(
    db: Database, tenant: TenantId, *, outage_started_at: datetime, now: datetime
) -> int:
    """Void the tenant's open nonces created before the outage. Returns how many."""
    async with db.tenant(tenant) as conn:
        cur = await conn.execute(
            "UPDATE velora.pending_actions SET voided_at = %s, void_reason = 'recovery' "
            "WHERE consumed_at IS NULL AND voided_at IS NULL AND created_at < %s "
            "RETURNING id, conversation_id, expires_at",
            (now, outage_started_at),
        )
        voided = await cur.fetchall()
        for action_id, conversation_id, expires_at in voided:
            if expires_at <= outage_started_at:
                continue  # it had expired already; the customer was waiting for nothing
            await send_to_customer(
                conn,
                tenant,
                conversation_id,
                copy.RECONFIRM_AFTER_OUTAGE,
                key=f"recovery-reconfirm:{action_id}",
                now=now,
            )
    return len(voided)
