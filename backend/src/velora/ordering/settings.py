"""Per-tenant ordering settings (P4-T6): delivery, pickup, tax, COD, transfers and capacity.

The row is created with the database defaults the first time it is needed. The limits here
match the table's CHECK constraints, which remain the final guard.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from velora.db import TenantId
from velora.identity.errors import InvalidInputError, expect_row


@dataclass(frozen=True)
class OrderingSettings:
    delivery_enabled: bool
    takeaway_enabled: bool
    delivery_fee_paisa: int
    delivery_min_order_paisa: int
    pickup_min_lead_minutes: int
    pickup_max_ahead_minutes: int
    # Basis points of the item subtotal. None: not set, so that method can't be quoted (B3).
    tax_cod_bps: int | None
    tax_transfer_bps: int | None
    cod_enabled: bool
    transfer_enabled: bool
    cod_first_order_limit_paisa: int | None
    transfer_window_minutes: int
    max_orders_per_slot: int | None
    paused_until: datetime | None
    pause_reason: str | None

    def is_paused(self, now: datetime) -> bool:
        return self.paused_until is not None and now < self.paused_until

    def editable(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k in BOUNDS or k in FLAGS}


_COLUMNS = ", ".join(f.name for f in fields(OrderingSettings))

FLAGS = frozenset({"delivery_enabled", "takeaway_enabled", "cod_enabled", "transfer_enabled"})

# Field -> (lowest, highest, may be None).
BOUNDS: dict[str, tuple[int, int, bool]] = {
    "delivery_fee_paisa": (0, 1_000_000, False),
    "delivery_min_order_paisa": (0, 10_000_000, False),
    "pickup_min_lead_minutes": (0, 240, False),
    "pickup_max_ahead_minutes": (1, 1440, False),
    "tax_cod_bps": (0, 5000, True),
    "tax_transfer_bps": (0, 5000, True),
    "cod_first_order_limit_paisa": (1, 100_000_000, True),
    "transfer_window_minutes": (5, 1440, False),
    "max_orders_per_slot": (1, 500, True),
}


async def load_settings(
    conn: AsyncConnection[Any], tenant: TenantId, *, lock: bool = False
) -> OrderingSettings:
    await conn.execute(
        "INSERT INTO velora.ordering_settings (tenant_id) VALUES (%s) "
        "ON CONFLICT (tenant_id) DO NOTHING",
        (tenant.value,),
    )
    cur = await conn.execute(
        f"SELECT {_COLUMNS} FROM velora.ordering_settings"  # noqa: S608
        + (" FOR UPDATE" if lock else "")
    )
    return OrderingSettings(*expect_row(await cur.fetchone()))


def apply_changes(current: OrderingSettings, changes: dict[str, Any]) -> OrderingSettings:
    """`current` with `changes` applied, after checking every value. Raises InvalidInputError."""
    for name, value in changes.items():
        if name in FLAGS:
            if not isinstance(value, bool):
                raise InvalidInputError(f"{name} must be true or false")
        elif name in BOUNDS:
            low, high, nullable = BOUNDS[name]
            if value is None:
                if not nullable:
                    raise InvalidInputError(f"{name} can't be empty")
            elif isinstance(value, bool) or not isinstance(value, int):
                raise InvalidInputError(f"{name} must be a whole number")
            elif not low <= value <= high:
                raise InvalidInputError(f"{name} must be between {low} and {high}")
        else:
            raise InvalidInputError(f"{name} is not a setting you can change here")
    updated = replace(current, **changes)
    if not (updated.delivery_enabled or updated.takeaway_enabled):
        raise InvalidInputError("keep delivery or takeaway switched on")
    if not (updated.cod_enabled or updated.transfer_enabled):
        raise InvalidInputError("keep cash on delivery or transfer switched on")
    if updated.pickup_max_ahead_minutes < updated.pickup_min_lead_minutes:
        raise InvalidInputError("the latest pickup time can't be before the earliest")
    return updated
