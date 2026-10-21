"""P3-T1: founder onboarding through velora_platform, and the P3 schema guarantees.

Exit check: a tenant can't be marked ready for go-live without both a verification record and
a consent record, and the audit entries exist.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from psycopg import errors

from tests.db.conftest import PLATFORM_ROLE, PgDatabase
from velora.admin.__main__ import main
from velora.admin.onboarding import GoLiveBlockedError, Onboarding, OnboardingError
from velora.db import TenantId

SHA = "ab" * 32


@pytest.fixture()
def ops(db: PgDatabase) -> Iterator[Onboarding]:
    with db.platform() as conn:
        yield Onboarding(conn, operator="founder")


def new_tenant(ops: Onboarding) -> TenantId:
    return ops.create_tenant(
        slug=f"t-{uuid.uuid4().hex[:12]}", display_name="Karachi Grill", order_prefix="KG"
    )


def verify(ops: Onboarding, tenant: TenantId, status: Any = "verified", **kw: Any) -> uuid.UUID:
    return ops.record_verification(
        tenant, method="in_person", status=status, evidence_ref="founder-drive/kg/visit", **kw
    )


def consent(
    ops: Onboarding, tenant: TenantId, kind: Any = "separate_number_ban_risk", **kw: Any
) -> uuid.UUID:
    return ops.record_consent(
        tenant,
        kind=kind,
        document_version="2026-10",
        document_sha256=SHA,
        accepted_by_name="Owner Name",
        **kw,
    )


def status_of(db: PgDatabase, tenant: TenantId) -> str:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT status FROM velora.tenants WHERE id = %s", (tenant.value,)
        ).fetchone()
    assert row is not None
    return str(row[0])


# Go-live readiness ------------------------------------------------------------------------


def test_go_live_needs_both_a_verification_and_a_consent(ops: Onboarding, db: PgDatabase) -> None:
    tenant = new_tenant(ops)
    with pytest.raises(GoLiveBlockedError) as blocked:
        ops.go_live(tenant)
    assert blocked.value.gaps == ["owner_verification", "consent_separate_number_ban_risk"]

    verify(ops, tenant)
    with pytest.raises(GoLiveBlockedError) as blocked:
        ops.go_live(tenant)
    assert blocked.value.gaps == ["consent_separate_number_ban_risk"]

    consent(ops, tenant)
    assert ops.go_live_gaps(tenant) == []
    ops.go_live(tenant)
    assert status_of(db, tenant) == "active"

    with db.admin() as conn:
        rows = conn.execute(
            "SELECT action, actor_kind, details->>'operator' FROM velora.audit_log "
            "WHERE tenant_id = %s",
            (tenant.value,),
        ).fetchall()
    assert sorted(r[0] for r in rows) == [
        "consent.recorded",
        "owner_verification.recorded",
        "tenant.created",
        "tenant.went_live",
    ]
    assert {(r[1], r[2]) for r in rows} == {("platform", "founder")}


def test_consent_alone_is_not_enough(ops: Onboarding) -> None:
    tenant = new_tenant(ops)
    consent(ops, tenant)
    with pytest.raises(GoLiveBlockedError) as blocked:
        ops.go_live(tenant)
    assert blocked.value.gaps == ["owner_verification"]


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_only_a_verified_outcome_counts(ops: Onboarding, status: str) -> None:
    tenant = new_tenant(ops)
    verify(ops, tenant, status)
    consent(ops, tenant)
    assert ops.go_live_gaps(tenant) == ["owner_verification"]


def test_other_consent_kinds_do_not_count(ops: Onboarding) -> None:
    tenant = new_tenant(ops)
    verify(ops, tenant)
    consent(ops, tenant, "terms")
    assert ops.go_live_gaps(tenant) == ["consent_separate_number_ban_risk"]


def test_a_superseded_verification_no_longer_counts(ops: Onboarding) -> None:
    tenant = new_tenant(ops)
    first = verify(ops, tenant)
    consent(ops, tenant)
    assert ops.go_live_gaps(tenant) == []
    verify(ops, tenant, "rejected", supersedes=first)
    assert ops.go_live_gaps(tenant) == ["owner_verification"]


def test_a_record_can_be_superseded_only_once(ops: Onboarding) -> None:
    tenant = new_tenant(ops)
    first = verify(ops, tenant, "pending")
    verify(ops, tenant, supersedes=first)
    with pytest.raises(errors.UniqueViolation):
        verify(ops, tenant, "rejected", supersedes=first)


def test_the_database_refuses_go_live_even_without_the_tool(
    db: PgDatabase, ops: Onboarding
) -> None:
    tenant = new_tenant(ops)
    with db.platform() as conn, conn.transaction(force_rollback=True):
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant.value),))
        with pytest.raises(errors.CheckViolation, match="cannot go live"):
            conn.execute(
                "UPDATE velora.tenants SET status = 'active' WHERE id = %s", (tenant.value,)
            )
    born_active = TenantId(uuid.uuid4())
    with db.platform() as conn, conn.transaction(force_rollback=True):
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(born_active.value),))
        with pytest.raises(errors.CheckViolation, match="cannot go live"):
            conn.execute(
                "INSERT INTO velora.tenants (id, slug, display_name, order_number_prefix, status) "
                "VALUES (%s, %s, 'X', 'XX', 'active')",
                (born_active.value, f"t-{uuid.uuid4().hex[:12]}"),
            )


def test_the_runtime_role_cannot_change_a_tenants_status(db: PgDatabase, ops: Onboarding) -> None:
    tenant = new_tenant(ops)
    with db.app() as conn, conn.transaction(force_rollback=True):
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant.value),))
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("UPDATE velora.tenants SET status = 'active'")


def test_going_live_twice_is_refused(ops: Onboarding) -> None:
    tenant = new_tenant(ops)
    verify(ops, tenant)
    consent(ops, tenant)
    ops.go_live(tenant)
    with pytest.raises(OnboardingError, match="still onboarding"):
        ops.go_live(tenant)


# Immutable records ------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["consents", "owner_verifications", "audit_log"])
def test_records_cannot_be_edited_or_deleted_even_by_a_superuser(
    db: PgDatabase, ops: Onboarding, table: str
) -> None:
    tenant = new_tenant(ops)
    verify(ops, tenant)
    consent(ops, tenant)
    for statement in (
        f"UPDATE velora.{table} SET tenant_id = tenant_id WHERE tenant_id = %s",  # noqa: S608
        f"DELETE FROM velora.{table} WHERE tenant_id = %s",  # noqa: S608
    ):
        with (
            db.admin() as conn,
            conn.transaction(force_rollback=True),
            pytest.raises(errors.RestrictViolation, match="record a correction"),
        ):
            conn.execute(statement.encode(), (tenant.value,))
    with (
        db.admin() as conn,
        conn.transaction(force_rollback=True),
        pytest.raises(errors.RestrictViolation),
    ):
        conn.execute(f"TRUNCATE velora.{table} CASCADE".encode())


@pytest.mark.parametrize("table", ["consents", "owner_verifications", "audit_log"])
def test_platform_role_has_no_update_or_delete_on_records(db: PgDatabase, table: str) -> None:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT has_table_privilege(%(r)s, %(t)s, 'UPDATE'), "
            "has_table_privilege(%(r)s, %(t)s, 'DELETE'), "
            "has_table_privilege(%(r)s, %(t)s, 'TRUNCATE')",
            {"r": PLATFORM_ROLE, "t": f"velora.{table}"},
        ).fetchone()
    assert row == (False, False, False)


# Platform role scope ----------------------------------------------------------------------


def test_platform_role_lists_tenants_but_works_one_tenant_at_a_time(
    db: PgDatabase, ops: Onboarding
) -> None:
    a, b = new_tenant(ops), new_tenant(ops)
    ops.invite_owner(a, display_name="Owner A")
    ops.invite_owner(b, display_name="Owner B")
    listed = {t.id for t in ops.list_tenants()}
    assert {a.value, b.value} <= listed

    with db.platform() as conn, conn.transaction(force_rollback=True):
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(a.value),))
        for table in ("staff_members", "magic_links", "audit_log"):
            owners = conn.execute(
                f"SELECT DISTINCT tenant_id FROM velora.{table}".encode()  # noqa: S608
            ).fetchall()
            assert owners == [(a.value,)], table
        with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
            conn.execute(
                "INSERT INTO velora.staff_members (tenant_id, display_name, role) "
                "VALUES (%s, 'Intruder', 'owner')",
                (b.value,),
            )

    with db.platform() as conn:
        assert conn.execute("SELECT count(*) FROM velora.staff_members").fetchone() == (0,)


@pytest.mark.parametrize("table", ["orders", "customers", "payments", "inbox_events"])
def test_platform_role_cannot_read_business_data(db: PgDatabase, table: str) -> None:
    with db.platform() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(f"SELECT count(*) FROM velora.{table}".encode())  # noqa: S608


def test_platform_role_is_not_privileged(db: PgDatabase) -> None:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolinherit, rolcanlogin FROM pg_roles "
            "WHERE rolname = %s",
            (PLATFORM_ROLE,),
        ).fetchone()
    assert row == (False, False, False, True)


# Other P3 schema rules --------------------------------------------------------------------


def test_magic_links_cannot_outlive_15_minutes(db: PgDatabase, ops: Onboarding) -> None:
    tenant = new_tenant(ops)
    owner = ops.invite_owner(tenant, display_name="Owner")
    now = datetime.now(UTC)
    with db.platform() as conn, conn.transaction(force_rollback=True):
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant.value),))
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "INSERT INTO velora.magic_links (tenant_id, staff_member_id, token_hash, purpose, "
                "issued_by_kind, created_at, expires_at) "
                "VALUES (%s, %s, %s, 'login', 'platform', %s, %s)",
                (tenant.value, owner.staff_id, b"x" * 32, now, now + timedelta(minutes=16)),
            )


def test_a_kitchen_device_cannot_hold_a_pin(db: PgDatabase, ops: Onboarding) -> None:
    tenant = new_tenant(ops)
    with db.app() as conn, conn.transaction(force_rollback=True):
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant.value),))
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "INSERT INTO velora.staff_members (tenant_id, display_name, role, pin_hash, "
                "pin_set_at) VALUES (%s, 'Tablet', 'kitchen_device', 'scrypt$x', now())",
                (tenant.value,),
            )


# Tool behaviour ---------------------------------------------------------------------------


def test_an_operator_name_is_required(db: PgDatabase) -> None:
    with db.platform() as conn, pytest.raises(OnboardingError):
        Onboarding(conn, operator="  ")


def test_duplicate_slug_is_reported(ops: Onboarding) -> None:
    slug = f"t-{uuid.uuid4().hex[:12]}"
    ops.create_tenant(slug=slug, display_name="One", order_prefix="ON")
    with pytest.raises(OnboardingError, match="already taken"):
        ops.create_tenant(slug=slug, display_name="Two", order_prefix="TW")


def test_recovery_link_only_for_an_active_member_of_that_tenant(ops: Onboarding) -> None:
    a, b = new_tenant(ops), new_tenant(ops)
    owner_a = ops.invite_owner(a, display_name="Owner A")
    assert ops.recovery_link(a, owner_a.staff_id).staff_id == owner_a.staff_id
    with pytest.raises(OnboardingError):
        ops.recovery_link(b, owner_a.staff_id)


def test_command_line_onboarding_flow(
    db: PgDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("VELORA_PLATFORM_DATABASE_URL", db.cluster.url(db.name, PLATFORM_ROLE))
    monkeypatch.setenv("VELORA_PUBLIC_BASE_URL", "https://dash.velora.test")
    slug = f"cli-{uuid.uuid4().hex[:10]}"
    signed = tmp_path / "consent.pdf"
    signed.write_bytes(b"signed consent naming the ban risk")

    def run(*args: str) -> tuple[int, str]:
        code = main(["--operator", "founder", *args])
        out = capsys.readouterr()
        return code, out.out + out.err

    assert run("tenant-create", "--slug", slug, "--name", "Lahore Bites", "--prefix", "LB")[0] == 0
    code, out = run("go-live", "--tenant", slug)
    assert code == 1 and "owner_verification" in out
    assert (
        run("verification-record", "--tenant", slug, "--method", "ntn", "--status", "verified")[0]
        == 0
    )
    assert (
        run(
            "consent-record",
            "--tenant",
            slug,
            "--kind",
            "separate_number_ban_risk",
            "--version",
            "2026-10",
            "--document",
            str(signed),
            "--accepted-by",
            "Owner",
        )[0]
        == 0
    )
    assert (
        run("channel-register", "--tenant", slug, "--provider", "fake", "--session-ref", slug)[0]
        == 0
    )
    code, out = run("owner-invite", "--tenant", slug, "--name", "Owner")
    assert code == 0 and "https://dash.velora.test/login#" in out
    assert run("go-live", "--tenant", slug) == (0, f"{slug} is live\n")
    code, out = run("tenants")
    assert code == 0 and any(slug in line and "active" in line for line in out.splitlines())
    assert main(["tenants"]) == 1  # no operator name
    capsys.readouterr()

    with db.admin() as conn:
        row = conn.execute(
            "SELECT document_sha256 FROM velora.consents c JOIN velora.tenants t "
            "ON t.id = c.tenant_id WHERE t.slug = %s",
            (slug,),
        ).fetchone()
    assert row == (hashlib.sha256(signed.read_bytes()).hexdigest(),)
