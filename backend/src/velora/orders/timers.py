"""Order timers (P7-T4), run by the worker for each tenant with one due.

- **Transfer window:** an `awaiting_transfer` order whose `payment_due_at` has passed becomes
  `expired` (actor: system), and the customer is told (A8). A late payment is P8's staff review.
- **Nonce expiry** needs no timer: a YES nonce is refused once its `expires_at` has passed
  (`placement.claimable`, `placement.consume`), whatever the worker is doing.

Each order expires in its own savepoint, so one that a customer or staff member moved at the
same moment (`StaleTransitionError`) doesn't stop the others.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from velora.clock import Clock
from velora.db import Database, TenantId
from velora.orders.lifecycle import SYSTEM_ACTOR, StaleTransitionError, transition

log = logging.getLogger(__name__)

BATCH = 50


@dataclass
class TimerReport:
    expired: int = 0
    skipped: int = 0


class OrderTimers:
    def __init__(self, db: Database, clock: Clock, *, batch: int = BATCH) -> None:
        self._db = db
        self._clock = clock
        self._batch = batch

    async def run_once(self, tenant: TenantId) -> TimerReport:
        report = TimerReport()
        now = self._clock.now()
        async with self._db.tenant(tenant) as conn:
            cur = await conn.execute(
                "SELECT id FROM velora.orders WHERE status = 'awaiting_transfer' "
                "AND payment_due_at <= %s ORDER BY payment_due_at LIMIT %s",
                (now, self._batch),
            )
            for (order_id,) in await cur.fetchall():
                try:
                    async with conn.transaction():
                        await transition(
                            conn,
                            tenant,
                            order_id,
                            to="expired",
                            actor=SYSTEM_ACTOR,
                            now=now,
                            expected="awaiting_transfer",
                            reason="transfer window passed",
                        )
                except StaleTransitionError:
                    report.skipped += 1
                else:
                    report.expired += 1
        return report
