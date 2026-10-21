"""Cross-tenant work discovery for the workers (P5, AD-26 finding).

Runs as `velora_dispatcher`, which can read only tenant ids and queue-timing columns, never
a payload (migrations 0007 and 0009). It answers one question: which tenants have work due?
The worker then does that work as `app_rw` inside each tenant's RLS scope.
"""

from __future__ import annotations

from collections.abc import Sequence

from velora.clock import Clock
from velora.db import TenantId
from velora.db.access import ConnectionPool


class Dispatcher:
    def __init__(self, pool: ConnectionPool, clock: Clock) -> None:
        self._pool = pool
        self._clock = clock

    async def tenants_with_work(self, inbox_event_types: Sequence[str]) -> list[TenantId]:
        """Tenants with inbox events due for a handler we run, outbox messages to act on, or
        an order timer due (P7: a transfer window that has passed).

        Held messages count, so a reconnected channel is flushed on the next round.
        """
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """
                SELECT tenant_id FROM velora.inbox_events
                 WHERE event_type = ANY(%(types)s)
                   AND ((status = 'pending' AND available_at <= %(now)s)
                        OR (status = 'processing' AND locked_until <= %(now)s))
                UNION
                SELECT tenant_id FROM velora.outbox_messages
                 WHERE (status = 'pending' AND next_attempt_at <= %(now)s)
                    OR status = 'waiting_for_channel'
                    OR (status = 'sending' AND locked_until <= %(now)s)
                UNION
                SELECT tenant_id FROM velora.orders
                 WHERE status = 'awaiting_transfer' AND payment_due_at <= %(now)s
                """,
                {"types": list(inbox_event_types), "now": self._clock.now()},
            )
            rows = await cur.fetchall()
        return sorted((TenantId(r[0]) for r in rows), key=lambda t: t.value)

    async def tenants_with_channels(self) -> list[TenantId]:
        """Tenants with a channel in service, for the health poll."""
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT DISTINCT tenant_id FROM velora.channels WHERE state <> 'retired'"
            )
            rows = await cur.fetchall()
        return sorted((TenantId(r[0]) for r in rows), key=lambda t: t.value)
