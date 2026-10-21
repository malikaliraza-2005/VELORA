"""Ordering settings, pause and the customer-facing checks (P4-T5, P4-T6).

- Settings, pause and resume need `Permission.SETTINGS_EDIT` (owner, manager) and are audited
  with the old and new values.
- `check_new_cart`, `quote` and `claim_capacity` are for the conversation and order flows
  (P6, P7). They take the `TenantId` the bootstrap resolver gave for the channel, never one
  from message content.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection

from velora.clock import Clock
from velora.db import Database, TenantId
from velora.identity import audit
from velora.identity.errors import ForbiddenError, InvalidInputError
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.menu.store import live_menu
from velora.ordering import gate
from velora.ordering.pricing import CartLine, Fulfilment, PaymentMethod, Quote, QuoteRejected
from velora.ordering.pricing import price_cart as _price_cart
from velora.ordering.settings import OrderingSettings, apply_changes, load_settings

MAX_PAUSE = timedelta(hours=12)


@dataclass(frozen=True)
class OrderingStatus:
    """What the dashboard shows: whether new orders are accepted, and the slot's load."""

    decision: gate.GateDecision
    paused_until: datetime | None
    pause_reason: str | None
    slot_start: datetime
    slot_orders: int
    max_orders_per_slot: int | None


def _require(actor: StaffSession, permission: Permission) -> None:
    if not actor.can(permission):
        raise ForbiddenError(f"your role can't do this ({permission})")


class OrderingService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    # Staff --------------------------------------------------------------------------------

    async def settings(self, actor: StaffSession) -> OrderingSettings:
        _require(actor, Permission.SETTINGS_EDIT)
        async with self._db.tenant(actor.tenant) as conn:
            return await load_settings(conn, actor.tenant)

    async def update_settings(
        self, actor: StaffSession, changes: dict[str, Any]
    ) -> OrderingSettings:
        _require(actor, Permission.SETTINGS_EDIT)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            current = await load_settings(conn, actor.tenant, lock=True)
            updated = apply_changes(current, changes)
            before, after = current.editable(), updated.editable()
            changed = {
                k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]
            }
            if not changed:
                return current
            assignments = ", ".join(f"{name} = %({name})s" for name in changed)
            await conn.execute(
                f"UPDATE velora.ordering_settings SET {assignments}, "  # noqa: S608 - names from BOUNDS/FLAGS/TEXTS
                "updated_at = %(now)s",
                {**{k: after[k] for k in changed}, "now": now},
            )
            await self._audit(conn, actor, now, "settings.ordering_updated", {"changes": changed})
        return updated

    async def pause(
        self, actor: StaffSession, *, minutes: int, reason: str | None = None
    ) -> OrderingSettings:
        """Stop new carts and orders for `minutes`. Pausing again replaces the end time."""
        _require(actor, Permission.SETTINGS_EDIT)
        if not 1 <= minutes <= MAX_PAUSE.total_seconds() // 60:
            raise InvalidInputError(
                f"pause for 1 to {int(MAX_PAUSE.total_seconds() // 60)} minutes"
            )
        reason = (reason or "").strip() or None
        if reason is not None and len(reason) > 200:
            raise InvalidInputError("a pause reason is at most 200 characters")
        now = self._clock.now()
        until = now + timedelta(minutes=minutes)
        async with self._db.tenant(actor.tenant) as conn:
            current = await load_settings(conn, actor.tenant, lock=True)
            await conn.execute(
                "UPDATE velora.ordering_settings SET paused_until = %s, pause_reason = %s, "
                "updated_at = %s",
                (until, reason, now),
            )
            await self._audit(
                conn,
                actor,
                now,
                "ordering.paused",
                {"minutes": minutes, "until": until.isoformat(), "reason": reason},
            )
        return replace(current, paused_until=until, pause_reason=reason)

    async def resume(self, actor: StaffSession) -> OrderingSettings:
        _require(actor, Permission.SETTINGS_EDIT)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            current = await load_settings(conn, actor.tenant, lock=True)
            if not current.is_paused(now):
                return current
            await conn.execute(
                "UPDATE velora.ordering_settings SET paused_until = NULL, pause_reason = NULL, "
                "updated_at = %s",
                (now,),
            )
            await self._audit(
                conn,
                actor,
                now,
                "ordering.resumed",
                {"was_paused_until": str(current.paused_until)},
            )
        return replace(current, paused_until=None, pause_reason=None)

    async def status(self, actor: StaffSession) -> OrderingStatus:
        _require(actor, Permission.ORDERS_VIEW)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            decision = await gate.check_new_cart(conn, actor.tenant, now)
            settings = await load_settings(conn, actor.tenant)
            used = await gate.slot_orders(conn, now)
        return OrderingStatus(
            decision,
            settings.paused_until if settings.is_paused(now) else None,
            settings.pause_reason if settings.is_paused(now) else None,
            gate.slot_start(now),
            used,
            settings.max_orders_per_slot,
        )

    async def _audit(
        self,
        conn: AsyncConnection[Any],
        actor: StaffSession,
        now: datetime,
        action: str,
        details: dict[str, Any],
    ) -> None:
        await audit.record(
            conn,
            actor.tenant,
            at=now,
            actor_kind="staff",
            actor_id=actor.staff_id,
            action=action,
            target_type="tenant",
            target_id=actor.tenant.value,
            details=details,
        )

    # Customer-facing (P6, P7) -------------------------------------------------------------

    async def check_new_cart(self, tenant: TenantId) -> gate.GateDecision:
        async with self._db.tenant(tenant) as conn:
            return await gate.check_new_cart(conn, tenant, self._clock.now())

    async def quote(
        self,
        tenant: TenantId,
        lines: list[CartLine],
        *,
        fulfilment: Fulfilment,
        payment_method: PaymentMethod,
    ) -> Quote:
        async with self._db.tenant(tenant) as conn:
            live = await live_menu(conn)
            settings = await load_settings(conn, tenant)
        if live is None:
            raise QuoteRejected("no_menu", "the menu isn't available yet")
        return _price_cart(
            live, settings, lines, fulfilment=fulfilment, payment_method=payment_method
        )

    async def claim_capacity(self, tenant: TenantId) -> bool:
        """Standalone claim, committed at once. P7 uses `gate.claim_capacity` inside the
        order transaction instead, so a failed order doesn't keep its place."""
        async with self._db.tenant(tenant) as conn:
            settings = await load_settings(conn, tenant)
            return await gate.claim_capacity(conn, tenant, settings, self._clock.now())
