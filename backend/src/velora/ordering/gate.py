"""Whether a tenant takes new orders right now: status, pause, menu and capacity (P4-T5).

`check_new_cart` reads the database on every call, with no cache, so a pause applies to the
very next cart. `claim_capacity` takes a place in the current 15-minute slot atomically; P7
calls it inside the order transaction, so concurrent orders can't overshoot the cap.

Slots are aligned to 15 minutes since the Unix epoch, which are also quarter hours in
Pakistan time (UTC+5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection

from velora.db import TenantId
from velora.identity.errors import expect_row
from velora.menu.store import get_live_version
from velora.ordering.settings import OrderingSettings, load_settings

SLOT = timedelta(minutes=15)

ClosedReason = Literal["restaurant_unavailable", "paused", "no_menu", "at_capacity"]


@dataclass(frozen=True)
class GateDecision:
    open: bool
    reason: ClosedReason | None = None
    # When a temporary closure ends, if known.
    reopens_at: datetime | None = None


def slot_start(at: datetime) -> datetime:
    seconds = int(at.timestamp())
    return datetime.fromtimestamp(seconds - seconds % int(SLOT.total_seconds()), UTC)


async def slot_orders(conn: AsyncConnection[Any], at: datetime) -> int:
    cur = await conn.execute(
        "SELECT orders_count FROM velora.order_capacity_slots WHERE slot_start = %s",
        (slot_start(at),),
    )
    row = await cur.fetchone()
    return 0 if row is None else int(row[0])


async def check_new_cart(
    conn: AsyncConnection[Any], tenant: TenantId, now: datetime
) -> GateDecision:
    cur = await conn.execute("SELECT status FROM velora.tenants")
    (status,) = expect_row(await cur.fetchone())
    if status in ("paused", "closed"):
        return GateDecision(False, "restaurant_unavailable")
    settings = await load_settings(conn, tenant)
    if settings.is_paused(now):
        return GateDecision(False, "paused", settings.paused_until)
    if await get_live_version(conn) is None:
        return GateDecision(False, "no_menu")
    cap = settings.max_orders_per_slot
    if cap is not None and await slot_orders(conn, now) >= cap:
        return GateDecision(False, "at_capacity", slot_start(now) + SLOT)
    return GateDecision(True)


async def claim_capacity(
    conn: AsyncConnection[Any], tenant: TenantId, settings: OrderingSettings, now: datetime
) -> bool:
    """Count one order in the current slot. False, with nothing counted, if the slot is full."""
    cur = await conn.execute(
        "INSERT INTO velora.order_capacity_slots AS s (tenant_id, slot_start, orders_count) "
        "VALUES (%(tenant)s, %(slot)s, 1) ON CONFLICT (tenant_id, slot_start) DO UPDATE "
        "SET orders_count = s.orders_count + 1 "
        "WHERE %(cap)s::integer IS NULL OR s.orders_count < %(cap)s::integer "
        "RETURNING orders_count",
        {"tenant": tenant.value, "slot": slot_start(now), "cap": settings.max_orders_per_slot},
    )
    return await cur.fetchone() is not None
