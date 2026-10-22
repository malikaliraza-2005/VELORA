"""Staff management, tenant settings and payment PINs (P3-T4, P3-T5).

Every method takes the acting `StaffSession`, checks its permission, works only inside that
session's tenant, and writes an audit row in the same transaction as the change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import anyio
from psycopg import AsyncConnection

from velora.clock import Clock
from velora.db import Database
from velora.identity import audit
from velora.identity.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    PinRejectedError,
    expect_row,
)
from velora.identity.permissions import Permission, Role, allows, is_person
from velora.identity.pins import WeakPinError, hash_pin, validate_new_pin, verify_pin
from velora.identity.sessions import (
    IssuedLink,
    LinkPurpose,
    StaffSession,
    issue_magic_link,
    revoke_staff_sessions,
)

PIN_MAX_FAILURES = 5
PIN_LOCKOUT = timedelta(minutes=15)

_PAYMENT_PIN_REJECTED = "payment.pin_rejected"


@dataclass(frozen=True, slots=True)
class StaffMember:
    id: uuid.UUID
    display_name: str
    role: Role
    active: bool
    has_pin: bool


# Columns read into a StaffMember, in this order.


def _member(row: tuple[Any, ...]) -> StaffMember:
    return StaffMember(row[0], row[1], Role(row[2]), row[3], row[4])


def _require(actor: StaffSession, permission: Permission) -> None:
    if not actor.can(permission):
        raise ForbiddenError(f"your role can't do this ({permission})")


def _clean_name(display_name: str) -> str:
    name = display_name.strip()
    if not 1 <= len(name) <= 200:
        raise InvalidInputError("a name is 1 to 200 characters")
    return name


class StaffService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    # Reading ------------------------------------------------------------------------------

    async def list_staff(self, actor: StaffSession) -> list[StaffMember]:
        _require(actor, Permission.STAFF_VIEW)
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT id, display_name, role, active, pin_hash IS NOT NULL "
                "FROM velora.staff_members ORDER BY active DESC, created_at"
            )
            return [_member(row) for row in await cur.fetchall()]

    async def confirmers(self, actor: StaffSession) -> list[StaffMember]:
        """Who can confirm a payment with their PIN: the picker on a shared kitchen device
        (P9-T3). Names only; anyone who can act on orders may see them."""
        _require(actor, Permission.ORDERS_UPDATE)
        roles = sorted(r.value for r in Role if allows(r, Permission.PAYMENTS_CONFIRM))
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT id, display_name, role, active, pin_hash IS NOT NULL "
                "FROM velora.staff_members WHERE active AND pin_hash IS NOT NULL "
                "AND role = ANY(%s) ORDER BY display_name, id",
                (roles,),
            )
            return [_member(row) for row in await cur.fetchall()]

    async def _get(self, conn: AsyncConnection[Any], staff_id: uuid.UUID) -> StaffMember:
        cur = await conn.execute(
            "SELECT id, display_name, role, active, pin_hash IS NOT NULL "
            "FROM velora.staff_members WHERE id = %s FOR UPDATE",
            (staff_id,),
        )
        row = await cur.fetchone()
        if row is None:
            # Also the answer for another tenant's staff: RLS hides them.
            raise NotFoundError("no such staff member")
        return _member(row)

    # Invites and login links (AD-12: staff are added only by owner invite) ---------------

    async def invite(
        self, actor: StaffSession, *, display_name: str, role: Role
    ) -> tuple[StaffMember, IssuedLink]:
        _require(actor, Permission.STAFF_MANAGE)
        name = _clean_name(display_name)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "INSERT INTO velora.staff_members (tenant_id, display_name, role, created_at, "
                "updated_at) VALUES (%s, %s, %s, %s, %s) "
                "RETURNING id, display_name, role, active, pin_hash IS NOT NULL",
                (actor.tenant.value, name, role.value, now, now),
            )
            member = _member(expect_row(await cur.fetchone()))
            link = await self._issue(conn, actor, member.id, "invite", now)
            await self._audit(
                conn,
                actor,
                now,
                "staff.invited",
                member.id,
                {"role": role.value, "magic_link_id": str(link.link_id)},
            )
        return member, link

    async def issue_login_link(self, actor: StaffSession, staff_id: uuid.UUID) -> IssuedLink:
        """A fresh login link for an existing staff member, e.g. for a new device."""
        _require(actor, Permission.STAFF_MANAGE)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            member = await self._get(conn, staff_id)
            if not member.active:
                raise ConflictError("this staff member has been removed")
            link = await self._issue(conn, actor, member.id, "login", now)
            await self._audit(
                conn,
                actor,
                now,
                "staff.login_link_issued",
                member.id,
                {"magic_link_id": str(link.link_id)},
            )
        return link

    async def _issue(
        self,
        conn: AsyncConnection[Any],
        actor: StaffSession,
        staff_id: uuid.UUID,
        purpose: LinkPurpose,
        now: datetime,
    ) -> IssuedLink:
        return await issue_magic_link(
            conn,
            actor.tenant,
            staff_id,
            purpose=purpose,
            issued_by_staff_id=actor.staff_id,
            now=now,
        )

    # Role changes and removal -------------------------------------------------------------

    async def change_role(
        self, actor: StaffSession, staff_id: uuid.UUID, role: Role
    ) -> StaffMember:
        _require(actor, Permission.STAFF_MANAGE)
        if staff_id == actor.staff_id:
            raise ConflictError("you can't change your own role")
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            member = await self._get(conn, staff_id)
            if not member.active:
                raise ConflictError("this staff member has been removed")
            if member.role is role:
                return member
            # A kitchen tablet holds no personal PIN (also a database CHECK).
            clear_pin = not is_person(role)
            cur = await conn.execute(
                "UPDATE velora.staff_members SET role = %(role)s, updated_at = %(now)s, "
                "pin_hash = CASE WHEN %(clear)s THEN NULL ELSE pin_hash END, "
                "pin_set_at = CASE WHEN %(clear)s THEN NULL ELSE pin_set_at END "
                "WHERE id = %(id)s RETURNING id, display_name, role, active, pin_hash IS NOT NULL",
                {"role": role.value, "now": now, "clear": clear_pin, "id": staff_id},
            )
            row = expect_row(await cur.fetchone())
            revoked = await revoke_staff_sessions(conn, staff_id, reason="role_changed", now=now)
            await self._audit(
                conn,
                actor,
                now,
                "staff.role_changed",
                staff_id,
                {"from": member.role.value, "to": role.value, "sessions_revoked": revoked},
            )
        return _member(row)

    async def remove(self, actor: StaffSession, staff_id: uuid.UUID) -> None:
        """Deactivate a staff member, end their sessions and void their unused links."""
        _require(actor, Permission.STAFF_MANAGE)
        if staff_id == actor.staff_id:
            raise ConflictError("you can't remove yourself")
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            member = await self._get(conn, staff_id)
            if not member.active:
                return
            await conn.execute(
                "UPDATE velora.staff_members SET active = false, updated_at = %s WHERE id = %s",
                (now, staff_id),
            )
            revoked = await revoke_staff_sessions(conn, staff_id, reason="staff_removed", now=now)
            await conn.execute(
                "UPDATE velora.magic_links SET consumed_at = %s "
                "WHERE staff_member_id = %s AND consumed_at IS NULL",
                (now, staff_id),
            )
            await self._audit(
                conn, actor, now, "staff.removed", staff_id, {"sessions_revoked": revoked}
            )

    async def revoke_sessions(self, actor: StaffSession, staff_id: uuid.UUID) -> int:
        """Owner revocation: log one staff member out on every device."""
        _require(actor, Permission.STAFF_MANAGE)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            await self._get(conn, staff_id)
            revoked = await revoke_staff_sessions(conn, staff_id, reason="owner_revoked", now=now)
            await self._audit(
                conn, actor, now, "staff.sessions_revoked", staff_id, {"sessions_revoked": revoked}
            )
        return revoked

    # Settings -------------------------------------------------------------------------------

    async def update_settings(self, actor: StaffSession, *, display_name: str) -> None:
        _require(actor, Permission.SETTINGS_EDIT)
        name = _clean_name(display_name)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            await conn.execute(
                "UPDATE velora.tenants SET display_name = %s, updated_at = %s WHERE id = %s",
                (name, now, actor.tenant.value),
            )
            await audit.record(
                conn,
                actor.tenant,
                at=now,
                actor_kind="staff",
                actor_id=actor.staff_id,
                action="settings.updated",
                target_type="tenant",
                target_id=actor.tenant.value,
                details={"fields": ["display_name"]},
            )

    # Payment PINs ---------------------------------------------------------------------------

    async def set_own_pin(
        self, actor: StaffSession, *, current_pin: str | None, new_pin: str
    ) -> None:
        if not is_person(actor.role):
            raise ForbiddenError("a kitchen device has no personal PIN")
        try:
            validate_new_pin(new_pin)
        except WeakPinError as exc:
            raise InvalidInputError(str(exc)) from exc
        new_hash = await anyio.to_thread.run_sync(hash_pin, new_pin)
        now = self._clock.now()
        rejected: str | None = None
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT pin_hash, pin_locked_until FROM velora.staff_members "
                "WHERE id = %s FOR UPDATE",
                (actor.staff_id,),
            )
            stored, locked_until = expect_row(await cur.fetchone())
            if stored is not None:
                rejected = await self._check_pin(
                    conn,
                    actor,
                    actor.staff_id,
                    current_pin or "",
                    stored,
                    locked_until,
                    now,
                    action="staff.pin_change_rejected",
                )
            if rejected is None:
                await conn.execute(
                    "UPDATE velora.staff_members SET pin_hash = %s, pin_set_at = %s, "
                    "pin_failed_attempts = 0, pin_locked_until = NULL, updated_at = %s "
                    "WHERE id = %s",
                    (new_hash, now, now, actor.staff_id),
                )
                await self._audit(
                    conn,
                    actor,
                    now,
                    "staff.pin_set",
                    actor.staff_id,
                    {"replaced": stored is not None},
                )
        if rejected is not None:
            raise PinRejectedError(rejected)

    async def reset_pin(self, actor: StaffSession, staff_id: uuid.UUID) -> None:
        """Owner recovery for a forgotten or locked PIN: clear it so the person sets a new one."""
        _require(actor, Permission.STAFF_MANAGE)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            await self._get(conn, staff_id)
            await conn.execute(
                "UPDATE velora.staff_members SET pin_hash = NULL, pin_set_at = NULL, "
                "pin_failed_attempts = 0, pin_locked_until = NULL, updated_at = %s WHERE id = %s",
                (now, staff_id),
            )
            await self._audit(conn, actor, now, "staff.pin_reset", staff_id, {})

    async def verify_payment_pin(
        self, actor: StaffSession, *, pin: str, confirmer_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        """Check the personal PIN that must accompany a payment confirmation (AD-23, P8).

        On a personal session the confirmer is the signed-in person. On a shared kitchen
        device the request names the confirmer, who must hold `PAYMENTS_CONFIRM`. Returns the
        confirmer's staff id. A failure is counted, audited and committed before
        `PinRejectedError` is raised.
        """
        if is_person(actor.role):
            if confirmer_id not in (None, actor.staff_id):
                raise ForbiddenError("confirm payments with your own PIN")
            confirmer_id = actor.staff_id
        elif confirmer_id is None:
            raise InvalidInputError("choose who is confirming the payment")

        now = self._clock.now()
        rejected: str | None = None
        async with self._db.tenant(actor.tenant) as conn:
            cur = await conn.execute(
                "SELECT role, active, pin_hash, pin_locked_until FROM velora.staff_members "
                "WHERE id = %s FOR UPDATE",
                (confirmer_id,),
            )
            row = await cur.fetchone()
            if row is None or not row[1]:
                rejected = "unknown_confirmer"
            elif not allows(Role(row[0]), Permission.PAYMENTS_CONFIRM):
                rejected = "not_permitted"
            elif row[2] is None:
                rejected = "no_pin_set"
            else:
                rejected = await self._check_pin(
                    conn,
                    actor,
                    confirmer_id,
                    pin,
                    row[2],
                    row[3],
                    now,
                    action=_PAYMENT_PIN_REJECTED,
                )
            if rejected in ("unknown_confirmer", "not_permitted", "no_pin_set"):
                await self._audit_pin_rejected(
                    conn, actor, confirmer_id, rejected, now, action=_PAYMENT_PIN_REJECTED
                )
        if rejected is not None:
            raise PinRejectedError(rejected)
        return confirmer_id

    async def _check_pin(
        self,
        conn: AsyncConnection[Any],
        actor: StaffSession,
        staff_id: uuid.UUID,
        pin: str,
        stored: str,
        locked_until: datetime | None,
        now: datetime,
        *,
        action: str,
    ) -> str | None:
        """Verify `pin` for a row the caller has locked. Counts and audits failures."""
        if locked_until is not None and now < locked_until:
            await self._audit_pin_rejected(conn, actor, staff_id, "locked", now, action=action)
            return "locked"
        if await anyio.to_thread.run_sync(verify_pin, pin, stored):
            await conn.execute(
                "UPDATE velora.staff_members SET pin_failed_attempts = 0, pin_locked_until = NULL "
                "WHERE id = %s AND (pin_failed_attempts <> 0 OR pin_locked_until IS NOT NULL)",
                (staff_id,),
            )
            return None
        cur = await conn.execute(
            "UPDATE velora.staff_members SET "
            "pin_failed_attempts = CASE WHEN pin_failed_attempts + 1 >= %(max)s THEN 0 "
            "                           ELSE pin_failed_attempts + 1 END, "
            "pin_locked_until = CASE WHEN pin_failed_attempts + 1 >= %(max)s "
            "                        THEN %(until)s::timestamptz ELSE NULL END "
            "WHERE id = %(id)s RETURNING pin_locked_until",
            {"max": PIN_MAX_FAILURES, "until": now + PIN_LOCKOUT, "id": staff_id},
        )
        row = await cur.fetchone()
        reason = "wrong_pin_now_locked" if row is not None and row[0] is not None else "wrong_pin"
        await self._audit_pin_rejected(conn, actor, staff_id, reason, now, action=action)
        return reason

    async def _audit_pin_rejected(
        self,
        conn: AsyncConnection[Any],
        actor: StaffSession,
        staff_id: uuid.UUID | None,
        reason: str,
        now: datetime,
        *,
        action: str,
    ) -> None:
        await audit.record(
            conn,
            actor.tenant,
            at=now,
            actor_kind="staff",
            actor_id=actor.staff_id,
            action=action,
            target_type="staff_member",
            target_id=staff_id,
            details={"reason": reason, "session_id": str(actor.session_id)},
        )

    async def _audit(
        self,
        conn: AsyncConnection[Any],
        actor: StaffSession,
        now: datetime,
        action: str,
        staff_id: uuid.UUID,
        details: dict[str, Any],
    ) -> None:
        await audit.record(
            conn,
            actor.tenant,
            at=now,
            actor_kind="staff",
            actor_id=actor.staff_id,
            action=action,
            target_type="staff_member",
            target_id=staff_id,
            details=details,
        )
