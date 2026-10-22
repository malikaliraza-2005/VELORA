"""P10: `velora_monitor` sees counts, states and timestamps across tenants, and nothing else.

It can't read a payload, a message, an address, an amount or a customer, can't change any
tenant's data, and can only append backup and alert records, which the runtime can't see.
"""

from __future__ import annotations

from typing import Any

import pytest
from psycopg import errors

from tests.db.conftest import PgDatabase
from tests.db.seed import seed_tenant


@pytest.fixture(scope="module")
def seeded(mondb: PgDatabase) -> list[Any]:
    with mondb.admin() as conn:
        return [
            seed_tenant(conn, "mon-a", "MA")["tenants"],
            seed_tenant(conn, "mon-b", "MB")["tenants"],
        ]


ALLOWED = [
    "SELECT id, slug, display_name, status FROM velora.tenants",
    "SELECT id, tenant_id, provider, state, state_changed_at FROM velora.channels",
    "SELECT tenant_id, channel_id, event_type, details, created_at FROM velora.channel_events",
    "SELECT tenant_id, event_type, status, available_at, received_at FROM velora.inbox_events",
    "SELECT tenant_id, status, purpose, created_at, next_attempt_at FROM velora.outbox_messages",
    "SELECT tenant_id, conversation_id, status, payment_method, created_at FROM velora.orders",
    "SELECT tenant_id, status, verified_at FROM velora.payments",
    "SELECT tenant_id, conversation_id, created_at FROM velora.handoff_events",
    "SELECT tenant_id, reason, created_at FROM velora.ingress_rejections",
    "SELECT * FROM velora.global_pause_events",
    "SELECT * FROM velora.backup_runs",
    "SELECT * FROM velora.alert_events",
]


@pytest.mark.parametrize("query", ALLOWED)
def test_the_monitor_reads_its_columns(mondb: PgDatabase, seeded: list[Any], query: str) -> None:
    with mondb.monitor() as conn:
        conn.execute(query).fetchall()


def test_the_monitor_sees_every_tenant(mondb: PgDatabase, seeded: list[Any]) -> None:
    with mondb.monitor() as conn:
        seen = {r[0] for r in conn.execute("SELECT DISTINCT tenant_id FROM velora.orders")}
    assert set(seeded) <= seen


@pytest.mark.parametrize(
    "query",
    [
        "SELECT payload FROM velora.inbox_events",
        "SELECT payload FROM velora.outbox_messages",
        "SELECT last_error FROM velora.outbox_messages",
        "SELECT context FROM velora.conversations",
        "SELECT phone_e164 FROM velora.customers",
        "SELECT delivery_address FROM velora.orders",
        "SELECT total_paisa FROM velora.orders",
        "SELECT amount_paisa FROM velora.payments",
        "SELECT reference FROM velora.payments",
        "SELECT session_ref FROM velora.channels",
        "SELECT * FROM velora.audit_log",
        "SELECT * FROM velora.staff_members",
        "SELECT * FROM velora.payment_evidence",
        "SELECT reason FROM velora.handoff_events",
        "SELECT channel_id FROM velora.ingress_rejections",
    ],
)
def test_the_monitor_sees_no_content(mondb: PgDatabase, query: str) -> None:
    with mondb.monitor() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(query)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE velora.channels SET state = 'connected'",
        "UPDATE velora.orders SET status = 'completed'",
        "DELETE FROM velora.inbox_events",
        "INSERT INTO velora.global_pause_events (paused, operator) VALUES (false, 'x')",
        "UPDATE velora.alert_events SET delivered = true",
        "DELETE FROM velora.backup_runs",
    ],
)
def test_the_monitor_changes_nothing_but_its_own_records(mondb: PgDatabase, statement: str) -> None:
    with mondb.monitor() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement)


def test_backup_and_alert_records_are_append_only(mondb: PgDatabase) -> None:
    with mondb.monitor() as conn:
        conn.execute("INSERT INTO velora.backup_runs (ok, kind) VALUES (true, 'hourly-dump')")
        conn.execute(
            "INSERT INTO velora.alert_events (alert_key, severity, state, summary, delivered) "
            "VALUES ('backup_failed', 'critical', 'firing', 'x', true)"
        )
        conn.commit()
    with mondb.admin() as conn, pytest.raises(errors.RestrictViolation):
        conn.execute("DELETE FROM velora.backup_runs")


@pytest.mark.parametrize("table", ["backup_runs", "alert_events"])
def test_the_runtime_cannot_see_the_founders_records(mondb: PgDatabase, table: str) -> None:
    with mondb.app() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(f"SELECT * FROM velora.{table}")  # noqa: S608 - fixed table names


def test_the_monitor_role_is_not_privileged(mondb: PgDatabase) -> None:
    with mondb.admin() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit, rolcanlogin "
            "FROM pg_roles WHERE rolname = 'velora_monitor'"
        ).fetchone()
    assert row == (False, False, False, False, False, True)


@pytest.mark.parametrize(
    "values",
    [
        "(true, 'Hourly Dump')",  # kinds are short lowercase names
        "(true, 's3://bucket/key?token=abc')",  # never a path or URL
    ],
)
def test_backup_kinds_are_names_not_locations(mondb: PgDatabase, values: str) -> None:
    with mondb.monitor() as conn, pytest.raises(errors.CheckViolation):
        conn.execute(f"INSERT INTO velora.backup_runs (ok, kind) VALUES {values}")  # noqa: S608
