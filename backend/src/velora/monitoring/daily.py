"""Daily pilot metrics (P10-T4), per restaurant, for one Pakistan day (P13-T5 needs them).

- **orders** placed that day, and how many of them were completed;
- **completed without a human:** completed orders whose chat had no handoff to staff that
  day before the order was placed (staff still accept and prepare every order);
- **handoffs** to staff;
- **disconnects:** a channel leaving `connected`;
- **minutes needing a QR scan** (`needs_qr`), from the channel's recorded state changes;
- **payment verifications** and reversals;
- **dead letters:** failed inbox events, and failed or unknown outbox messages;
- **alerts:** distinct problems the founder was alerted to (all restaurants together;
  incidents themselves are logged by hand).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from psycopg import AsyncConnection

from velora.identity.errors import expect_row
from velora.monitoring.metrics import pk_day_bounds


@dataclass
class TenantDay:
    tenant_id: uuid.UUID
    tenant: str
    orders: int = 0
    completed: int = 0
    completed_without_handoff: int = 0
    cancelled: int = 0
    expired: int = 0
    handoffs: int = 0
    disconnects: int = 0
    needs_qr_minutes: int = 0
    payments_verified: int = 0
    payments_reversed: int = 0
    dead_letters: int = 0


@dataclass(frozen=True)
class DailyReport:
    day: date
    starts_at: datetime
    ends_at: datetime
    tenants: list[TenantDay] = field(default_factory=list)
    alerts: int = 0


def time_in_state(
    state_at_start: str,
    changes: list[tuple[datetime, str]],
    starts: datetime,
    ends: datetime,
    wanted: str,
) -> timedelta:
    """How long, between `starts` and `ends`, a channel spent in `wanted`. `changes` are the
    (time, new state) changes inside the window, in order."""
    total = timedelta(0)
    state, since = state_at_start, starts
    for at, new in changes:
        if state == wanted:
            total += at - since
        state, since = new, at
    if state == wanted:
        total += ends - since
    return total


async def collect_daily(conn: AsyncConnection[Any], day: date, now: datetime) -> DailyReport:
    """`conn` is a `velora_monitor` connection. A day still running counts up to `now`."""
    starts, ends = pk_day_bounds(day)
    until = min(ends, max(now, starts))
    params = {"starts": starts, "ends": ends}
    async with conn.transaction():
        await conn.execute("SET TRANSACTION READ ONLY")
        cur = await conn.execute(
            "SELECT id, slug FROM velora.tenants WHERE status <> 'closed' ORDER BY slug"
        )
        rows = {tid: TenantDay(tid, slug) for tid, slug in await cur.fetchall()}

        def row(tenant_id: uuid.UUID) -> TenantDay | None:
            return rows.get(tenant_id)

        cur = await conn.execute(
            """
            SELECT o.tenant_id, count(*),
                   count(*) FILTER (WHERE o.status = 'completed'),
                   count(*) FILTER (WHERE o.status = 'completed' AND NOT EXISTS (
                       SELECT 1 FROM velora.handoff_events h
                        WHERE h.tenant_id = o.tenant_id
                          AND h.conversation_id = o.conversation_id
                          AND h.created_at >= %(starts)s AND h.created_at <= o.created_at)),
                   count(*) FILTER (WHERE o.status IN ('cancelled', 'rejected')),
                   count(*) FILTER (WHERE o.status = 'expired')
              FROM velora.orders o
             WHERE o.created_at >= %(starts)s AND o.created_at < %(ends)s
               AND o.status NOT IN ('draft', 'awaiting_confirmation')
             GROUP BY o.tenant_id
            """,
            params,
        )
        for tid, placed, completed, unaided, cancelled, expired in await cur.fetchall():
            if (r := row(tid)) is not None:
                r.orders, r.completed, r.completed_without_handoff = placed, completed, unaided
                r.cancelled, r.expired = cancelled, expired

        cur = await conn.execute(
            "SELECT tenant_id, count(*) FROM velora.handoff_events "
            "WHERE created_at >= %(starts)s AND created_at < %(ends)s GROUP BY tenant_id",
            params,
        )
        for tid, n in await cur.fetchall():
            if (r := row(tid)) is not None:
                r.handoffs = n

        cur = await conn.execute(
            "SELECT tenant_id, "
            "count(*) FILTER (WHERE status = 'verified'), "
            "count(*) FILTER (WHERE status = 'reversed') "
            "FROM velora.payments WHERE verified_at >= %(starts)s AND verified_at < %(ends)s "
            "GROUP BY tenant_id",
            params,
        )
        for tid, verified, reversed_ in await cur.fetchall():
            if (r := row(tid)) is not None:
                r.payments_verified, r.payments_reversed = verified, reversed_

        cur = await conn.execute(
            """
            SELECT tenant_id, sum(n) FROM (
                SELECT tenant_id, count(*) AS n FROM velora.inbox_events
                 WHERE status = 'failed'
                   AND available_at >= %(starts)s AND available_at < %(ends)s
                 GROUP BY tenant_id
                UNION ALL
                SELECT tenant_id, count(*) FROM velora.outbox_messages
                 WHERE status IN ('failed', 'unknown')
                   AND created_at >= %(starts)s AND created_at < %(ends)s
                 GROUP BY tenant_id) d
             GROUP BY tenant_id
            """,
            params,
        )
        for tid, n in await cur.fetchall():
            if (r := row(tid)) is not None:
                r.dead_letters = int(n)

        # Channel history: the state at the start of the day, then each change in it.
        cur = await conn.execute(
            """
            SELECT c.tenant_id, c.id,
                   (SELECT e.details ->> 'to' FROM velora.channel_events e
                     WHERE e.tenant_id = c.tenant_id AND e.channel_id = c.id
                       AND e.event_type = 'state_changed' AND e.created_at < %(starts)s
                     ORDER BY e.created_at DESC LIMIT 1),
                   (SELECT e.details ->> 'from' FROM velora.channel_events e
                     WHERE e.tenant_id = c.tenant_id AND e.channel_id = c.id
                       AND e.event_type = 'state_changed' AND e.created_at >= %(starts)s
                     ORDER BY e.created_at LIMIT 1),
                   c.state
              FROM velora.channels c
            """,
            params,
        )
        start_states = {
            (tid, cid): before or first_from or current
            for tid, cid, before, first_from, current in await cur.fetchall()
        }
        cur = await conn.execute(
            "SELECT tenant_id, channel_id, created_at, details ->> 'from', details ->> 'to' "
            "FROM velora.channel_events WHERE event_type = 'state_changed' "
            "AND created_at >= %(starts)s AND created_at < %(ends)s "
            "ORDER BY created_at",
            params,
        )
        changes: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[datetime, str]]] = defaultdict(list)
        for tid, cid, at, old, new in await cur.fetchall():
            if at > until:
                continue
            changes[(tid, cid)].append((at, new))
            if old == "connected" and new != "connected" and (r := row(tid)) is not None:
                r.disconnects += 1
        for (tid, cid), state in start_states.items():
            if (r := row(tid)) is None or until <= starts:
                continue
            spent = time_in_state(state, changes.get((tid, cid), []), starts, until, "needs_qr")
            r.needs_qr_minutes += int(spent.total_seconds() // 60)

        cur = await conn.execute(
            "SELECT count(DISTINCT alert_key) FROM velora.alert_events WHERE state = 'firing' "
            "AND created_at >= %(starts)s AND created_at < %(ends)s",
            params,
        )
        (alerts,) = expect_row(await cur.fetchone())
    return DailyReport(day, starts, ends, list(rows.values()), int(alerts))
