"""Founder onboarding operations (P3-T1, AD-12, AD-18).

These run as `velora_platform`: NOBYPASSRLS, owning nothing, and scoped to one tenant at a time
by the same tenant expression as the runtime. Its only cross-tenant right is listing tenants.

- Owner verification and consent records are append-only (database triggers). A correction
  is a new record that names the one it supersedes.
- Going live needs an effective verified owner verification and an effective ban-risk
  consent. The database enforces it (`tenants_go_live` trigger); `go_live_gaps` explains it.
- Every operation writes an audit row with `actor_kind = 'platform'` and the operator's name.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg import errors
from psycopg.types.json import Jsonb

from velora.clock import Clock, SystemClock
from velora.db import TenantId
from velora.identity.sessions import MAGIC_LINK_TTL
from velora.identity.tokens import ScopedToken, new_token

VerificationMethod = Literal["cnic", "ntn", "business_document", "in_person", "other"]
VerificationStatus = Literal["pending", "verified", "rejected"]
ConsentKind = Literal["separate_number_ban_risk", "data_processing", "terms"]
Provider = Literal["fake", "openwa", "meta_cloud"]


class OnboardingError(RuntimeError):
    pass


class GoLiveBlockedError(OnboardingError):
    def __init__(self, gaps: list[str]) -> None:
        super().__init__("cannot go live; missing: " + ", ".join(gaps))
        self.gaps = gaps


@dataclass(frozen=True)
class TenantSummary:
    id: uuid.UUID
    slug: str
    display_name: str
    status: str
    gaps: list[str]


@dataclass(frozen=True)
class OwnerInvite:
    staff_id: uuid.UUID
    token: ScopedToken
    expires_at: datetime


class Onboarding:
    def __init__(
        self, conn: psycopg.Connection[Any], *, operator: str, clock: Clock | None = None
    ) -> None:
        operator = operator.strip()
        if not operator:
            raise OnboardingError("an operator name is required for the audit log")
        # Each operation is its own transaction, so the tenant context never leaks between them.
        conn.autocommit = True
        self._conn = conn
        self._operator = operator
        self._clock = clock or SystemClock()

    @contextmanager
    def _tenant(self, tenant: TenantId) -> Iterator[psycopg.Connection[Any]]:
        with self._conn.transaction():
            self._conn.execute(
                "SELECT pg_catalog.set_config('app.tenant_id', %s, true)", (str(tenant.value),)
            )
            yield self._conn

    def _audit(
        self,
        tenant: TenantId,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO velora.audit_log (tenant_id, actor_kind, actor_id, action, target_type, "
            "target_id, details, created_at) VALUES (%s, 'platform', NULL, %s, %s, %s, %s, %s)",
            (
                tenant.value,
                action,
                target_type,
                target_id,
                Jsonb({"operator": self._operator, **(details or {})}),
                self._clock.now(),
            ),
        )

    def _insert(self, query: str, params: tuple[Any, ...]) -> uuid.UUID:
        row = self._conn.execute(query.encode(), params).fetchone()
        if row is None or not isinstance(row[0], uuid.UUID):
            raise RuntimeError("expected an id from INSERT ... RETURNING")
        return row[0]

    # Tenants ------------------------------------------------------------------------------

    def tenant_by_slug(self, slug: str) -> TenantId:
        row = self._conn.execute(
            "SELECT id FROM velora.tenants WHERE slug = %s", (slug,)
        ).fetchone()
        if row is None:
            raise OnboardingError(f"no tenant with slug {slug!r}")
        return TenantId(row[0])

    def create_tenant(self, *, slug: str, display_name: str, order_prefix: str) -> TenantId:
        tenant = TenantId(uuid.uuid4())
        now = self._clock.now()
        try:
            with self._tenant(tenant):
                self._conn.execute(
                    "INSERT INTO velora.tenants (id, slug, display_name, order_number_prefix, "
                    "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (tenant.value, slug, display_name, order_prefix, now, now),
                )
                self._audit(tenant, "tenant.created", "tenant", tenant.value, {"slug": slug})
        except errors.UniqueViolation as exc:
            raise OnboardingError(f"slug {slug!r} is already taken") from exc
        except errors.CheckViolation as exc:
            raise OnboardingError(f"invalid tenant details: {exc.diag.constraint_name}") from exc
        return tenant

    def list_tenants(self) -> list[TenantSummary]:
        rows = self._conn.execute(
            "SELECT id, slug, display_name, status FROM velora.tenants ORDER BY created_at"
        ).fetchall()
        return [
            TenantSummary(id_, slug, name, status, self.go_live_gaps(TenantId(id_)))
            for id_, slug, name, status in rows
        ]

    def go_live_gaps(self, tenant: TenantId) -> list[str]:
        with self._tenant(tenant):
            row = self._conn.execute(
                "SELECT velora.tenant_go_live_gaps(%s)", (tenant.value,)
            ).fetchone()
        return [] if row is None else list(row[0])

    def go_live(self, tenant: TenantId) -> None:
        try:
            with self._tenant(tenant):
                cur = self._conn.execute(
                    "UPDATE velora.tenants SET status = 'active', updated_at = %s "
                    "WHERE id = %s AND status = 'onboarding'",
                    (self._clock.now(), tenant.value),
                )
                if cur.rowcount != 1:
                    raise OnboardingError("only a tenant that is still onboarding can go live")
                self._audit(tenant, "tenant.went_live", "tenant", tenant.value)
        except errors.CheckViolation as exc:
            raise GoLiveBlockedError(self.go_live_gaps(tenant)) from exc

    # Owner verification and consent (append-only) ---------------------------------------

    def record_verification(
        self,
        tenant: TenantId,
        *,
        method: VerificationMethod,
        status: VerificationStatus,
        evidence_ref: str | None,
        supersedes: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Record an owner verification outcome. `evidence_ref` points to where the evidence
        is held (never the document itself)."""
        now = self._clock.now()
        with self._tenant(tenant):
            record_id = self._insert(
                "INSERT INTO velora.owner_verifications (tenant_id, method, status, evidence_ref, "
                "reviewed_by, decided_at, created_at, supersedes_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    tenant.value,
                    method,
                    status,
                    evidence_ref,
                    self._operator,
                    None if status == "pending" else now,
                    now,
                    supersedes,
                ),
            )
            self._audit(
                tenant,
                "owner_verification.recorded",
                "owner_verification",
                record_id,
                {"method": method, "status": status, "supersedes": _str(supersedes)},
            )
        return record_id

    def record_consent(
        self,
        tenant: TenantId,
        *,
        kind: ConsentKind,
        document_version: str,
        document_sha256: str,
        accepted_by_name: str,
        supersedes: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Record a signed consent. The signed document itself is kept outside VELORA; its
        SHA-256 ties this record to that exact document."""
        with self._tenant(tenant):
            record_id = self._insert(
                "INSERT INTO velora.consents (tenant_id, kind, document_version, document_sha256, "
                "accepted_by_name, accepted_at, supersedes_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    tenant.value,
                    kind,
                    document_version,
                    document_sha256.lower(),
                    accepted_by_name,
                    self._clock.now(),
                    supersedes,
                ),
            )
            self._audit(
                tenant,
                "consent.recorded",
                "consent",
                record_id,
                {"kind": kind, "version": document_version, "supersedes": _str(supersedes)},
            )
        return record_id

    # Channel (linked to a WhatsApp session in P5) -----------------------------------------

    def register_channel(
        self, tenant: TenantId, *, provider: Provider, session_ref: str
    ) -> uuid.UUID:
        now = self._clock.now()
        try:
            with self._tenant(tenant):
                channel_id = self._insert(
                    "INSERT INTO velora.channels (tenant_id, provider, session_ref, created_at, "
                    "updated_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (tenant.value, provider, session_ref, now, now),
                )
                self._audit(
                    tenant, "channel.registered", "channel", channel_id, {"provider": provider}
                )
        except errors.UniqueViolation as exc:
            raise OnboardingError("that provider session is already registered") from exc
        return channel_id

    # Global pause (P5-T5, AD-19) ----------------------------------------------------------

    def set_global_pause(self, *, paused: bool, reason: str | None = None) -> uuid.UUID:
        """Stop (or restart) new orders for every tenant. Appends to `global_pause_events`."""
        if paused and not (reason and reason.strip()):
            raise OnboardingError("a reason is required to pause every tenant")
        with self._conn.transaction():
            return self._insert(
                "INSERT INTO velora.global_pause_events (paused, reason, operator, created_at) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (paused, reason.strip() if reason else None, self._operator, self._clock.now()),
            )

    def global_pause_status(self) -> tuple[bool, str | None, datetime | None]:
        """Paused, the reason and since when (the newest event), or not paused."""
        row = self._conn.execute(
            "SELECT paused, reason, created_at FROM velora.global_pause_events "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return (False, None, None) if row is None else (row[0], row[1], row[2])

    # Owner access and recovery (AD-12) ----------------------------------------------------

    def invite_owner(self, tenant: TenantId, *, display_name: str) -> OwnerInvite:
        now = self._clock.now()
        with self._tenant(tenant):
            staff_id = self._insert(
                "INSERT INTO velora.staff_members (tenant_id, display_name, role, created_at, "
                "updated_at) VALUES (%s, %s, 'owner', %s, %s) RETURNING id",
                (tenant.value, display_name, now, now),
            )
            invite = self._issue_link(tenant, staff_id, "invite", now)
            self._audit(tenant, "staff.owner_invited", "staff_member", staff_id)
        return invite

    def recovery_link(self, tenant: TenantId, staff_id: uuid.UUID) -> OwnerInvite:
        """Manual recovery (lost phone, new device): a fresh login link for an active member.
        Follow the recovery runbook and confirm who is asking before using this."""
        with self._tenant(tenant):
            row = self._conn.execute(
                "SELECT active FROM velora.staff_members WHERE id = %s", (staff_id,)
            ).fetchone()
            if row is None or not row[0]:
                raise OnboardingError("no active staff member with that id in this tenant")
            invite = self._issue_link(tenant, staff_id, "login", self._clock.now())
            self._audit(tenant, "staff.recovery_link_issued", "staff_member", staff_id)
        return invite

    def _issue_link(
        self, tenant: TenantId, staff_id: uuid.UUID, purpose: str, now: datetime
    ) -> OwnerInvite:
        token = new_token(tenant)
        expires_at = now + MAGIC_LINK_TTL
        self._conn.execute(
            "INSERT INTO velora.magic_links (tenant_id, staff_member_id, token_hash, purpose, "
            "issued_by_kind, created_at, expires_at) VALUES (%s, %s, %s, %s, 'platform', %s, %s)",
            (tenant.value, staff_id, token.hash, purpose, now, expires_at),
        )
        return OwnerInvite(staff_id, token, expires_at)


def _str(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)
