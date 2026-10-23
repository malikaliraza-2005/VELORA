"""P11-T1 to P11-T3: database backups, the session-volume policy, and the restore drill.

Exit criterion (restore drill): restored to a fresh database well within 4 hours (RTO);
data no older than 1 hour (RPO); roles, FORCE RLS and policies present; the cross-tenant
checks pass after the restore; a test order succeeds on the restored copy.
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import errors

from tests.backup.conftest import BackupDb, Keys
from tests.conversation.conftest import chat
from tests.db.conftest import MONITOR_ROLE, PgDatabase, new_database_name
from tests.db.test_restore import SNAPSHOT_QUERIES
from tests.messaging.conftest import open_env
from tests.orders.conftest import orders_of, to_summary
from velora.backup.__main__ import _run as backup_command
from velora.backup.config import BackupSettings, PgTools
from velora.backup.crypto import MAGIC, generate_keypair, secret_key_from_text
from velora.backup.job import DATABASE, MANIFEST, ROLES, BackupError, BackupResult, run_backup
from velora.backup.restore import (
    RestoreError,
    _check_roles,
    complete_backups,
    read_manifest,
    restore_backup,
    role_attributes,
    verify_restored,
)
from velora.backup.storage import LocalStorage
from velora.clock import FakeClock
from velora.messaging.providers.fake import FakeProvider

NOW = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)


def backup(bk: BackupDb, keys: Keys, store: Path, tools: PgTools, **kw: Any) -> BackupResult:
    return run_backup(
        database_url=bk.backup_url,
        recipient=keys.public,
        storage=LocalStorage(store),
        tools=tools,
        now=kw.pop("now", NOW),
        **kw,
    )


def restore(bk: BackupDb, keys: Keys, store: Path, tools: PgTools, target: str, **kw: Any) -> Any:
    return restore_backup(
        storage=LocalStorage(store),
        stamp=kw.pop("stamp", None),
        secret=kw.pop("secret", keys.secret),
        admin_url=bk.pg.cluster.admin_url,
        dbname=target,
        tools=tools,
        now=kw.pop("now", NOW + timedelta(minutes=5)),
    )


def snapshot(url: str) -> dict[str, list[tuple[Any, ...]]]:
    with psycopg.connect(url) as conn:
        return {n: conn.execute(q.encode()).fetchall() for n, q in SNAPSHOT_QUERIES.items()}


# The backup role --------------------------------------------------------------------------


def test_the_backup_role_reads_every_tenant_and_writes_nothing(bkdb: BackupDb) -> None:
    with bkdb.pg.admin() as conn:
        (everything,) = conn.execute("SELECT count(*) FROM velora.customers").fetchone() or (0,)
        attrs = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
            "WHERE rolname = 'velora_backup'"
        ).fetchone()
        owned = conn.execute(
            "SELECT count(*) FROM pg_class WHERE relowner = 'velora_backup'::regrole"
        ).fetchone()
    assert attrs == (False, True, False, False)
    assert owned == (0,)
    with bkdb.pg.backup() as conn:
        # No tenant set, yet every tenant's rows: a dump under RLS would miss them.
        assert conn.execute("SELECT count(*) FROM velora.customers").fetchone() == (everything,)
        assert everything >= 3
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO velora.backup_runs (ok, kind) VALUES (true, 'x')")


# P11-T1 and P11-T2 ------------------------------------------------------------------------


def test_a_backup_is_encrypted_and_holds_only_the_database(
    bkdb: BackupDb, keys: Keys, tools: PgTools, store_dir: Path, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    result = backup(bkdb, keys, store_dir, tools, work_dir=work)
    stamp = "20261023T010000Z"
    assert result.stamp == stamp
    # P11-T2: exactly the database's three files; nothing from OpenWA's session volume.
    assert LocalStorage(store_dir).list() == [
        f"{stamp}/{DATABASE}",
        f"{stamp}/{MANIFEST}",
        f"{stamp}/{ROLES}",
    ]
    assert result.tables >= 30 and result.rows > 0
    for path in (store_dir / stamp).iterdir():
        data = path.read_bytes()
        assert data.startswith(MAGIC)
        for plaintext in (b"PGDMP", b"CREATE ROLE", b"backup-a", b"+92", b"velora_owner"):
            assert plaintext not in data, (path.name, plaintext)
    # No plaintext, and in fact nothing at all, is left in the work directory.
    assert list(work.iterdir()) == []
    manifest = read_manifest(LocalStorage(store_dir), stamp, keys.secret)
    assert manifest["row_counts"]["velora.tenants"] == 3
    assert "velora.orders" in manifest["force_rls"]
    assert any(item.startswith("app_rw=c/") for item in manifest["database_acl"])


def test_a_failed_dump_leaves_no_complete_backup(
    bkdb: BackupDb, keys: Keys, store_dir: Path
) -> None:
    broken = PgTools(("python", "-c", "import sys; sys.exit(3)", "--"))
    with pytest.raises(BackupError):
        backup(bkdb, keys, store_dir, broken)
    assert complete_backups(LocalStorage(store_dir)) == []


def test_each_run_is_reported_to_the_monitor(
    bkdb: BackupDb, keys: Keys, tools: PgTools, store_dir: Path
) -> None:
    monitor_url = bkdb.pg.cluster.url(bkdb.pg.name, MONITOR_ROLE)
    settings = BackupSettings(
        env="test",
        database_url=bkdb.backup_url,
        public_key=keys.public_text,
        storage_url=store_dir.as_uri(),
        tools=tools,
        monitor_database_url=monitor_url,
    )
    with bkdb.pg.admin() as conn:
        (before,) = conn.execute("SELECT count(*) FROM velora.backup_runs").fetchone() or (0,)
    assert backup_command(settings) == 0
    wrong = BackupSettings(**{**settings.__dict__, "database_url": bkdb.backup_url + "x"})
    assert backup_command(wrong) == 1
    with bkdb.pg.admin() as conn:
        found = conn.execute(
            "SELECT ok, kind, detail FROM velora.backup_runs ORDER BY seq OFFSET %s", (before,)
        ).fetchall()
    assert [(ok, kind) for ok, kind, _ in found] == [(True, "hourly-dump"), (False, "hourly-dump")]
    assert re.match(r"\d{8}T\d{6}Z: \d+ tables, \d+ rows", found[0][2])
    assert bkdb.pg.cluster.passwords["velora_backup"] not in found[1][2]


# P11-T3: the restore drill ----------------------------------------------------------------


def test_restore_drill(bkdb: BackupDb, keys: Keys, tools: PgTools, store_dir: Path) -> None:
    pg = bkdb.pg
    backup(bkdb, keys, store_dir, tools)
    target = new_database_name("drill")
    try:
        report = restore(bkdb, keys, store_dir, tools, target)
        failed = [c for c in report.checks if not c.ok]
        assert failed == []
        assert [c.name for c in report.checks] == [
            "roles",
            "schema version",
            "row counts",
            "FORCE RLS and policies",
            "database grants",
            "tenant isolation (as app_rw)",
        ]
        isolation = report.checks[-1]
        assert re.match(r"\d+ tables x 3 tenants$", isolation.detail)
        # RTO and RPO targets (the drill's share of them).
        assert report.duration < timedelta(hours=4)
        assert report.data_age < timedelta(hours=1)
        # Roles, grants, FORCE RLS and policies are exactly those of the original.
        restored = PgDatabase(pg.cluster, target)
        assert snapshot(pg.cluster.url(target)) == snapshot(pg.cluster.url(pg.name))
        # A test order succeeds on the restored copy, through the real runtime role.
        assert _test_order(restored, bkdb) == "placed"
    finally:
        pg.cluster.drop_database(target)


def _test_order(restored: PgDatabase, bk: BackupDb) -> str:
    async def main() -> str:
        async with open_env(restored, FakeClock(NOW), FakeProvider()) as env:
            customer = chat(env, restored, bk.shop)
            await to_summary(customer)
            (placed,) = await customer.send("YES")
        assert placed.startswith("✅ Order"), placed
        assert len(orders_of(restored, customer)) == 1
        return "placed"

    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(main(), loop_factory=loop)


def test_a_restore_refuses_a_changed_file_and_creates_nothing(
    bkdb: BackupDb, keys: Keys, tools: PgTools, store_dir: Path
) -> None:
    result = backup(bkdb, keys, store_dir, tools)
    dump = store_dir / result.stamp / DATABASE
    data = bytearray(dump.read_bytes())
    data[200] ^= 1
    dump.write_bytes(bytes(data))
    target = new_database_name("tampered")
    with pytest.raises(RestoreError, match="doesn't match its manifest"):
        restore(bkdb, keys, store_dir, tools, target)
    with bkdb.pg.admin() as conn:
        assert (
            conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,)).fetchone()
            is None
        )


def test_a_restore_refuses_the_wrong_key(
    bkdb: BackupDb, keys: Keys, tools: PgTools, store_dir: Path
) -> None:
    backup(bkdb, keys, store_dir, tools)
    _, other = generate_keypair()
    with pytest.raises(RestoreError, match="wrong key"):
        restore(bkdb, keys, store_dir, tools, "velora_never", secret=secret_key_from_text(other))


def test_a_restore_never_overwrites_a_database(
    bkdb: BackupDb, keys: Keys, tools: PgTools, store_dir: Path
) -> None:
    backup(bkdb, keys, store_dir, tools)
    with pytest.raises(RestoreError, match="already exists"):
        restore(bkdb, keys, store_dir, tools, bkdb.pg.name)


def test_only_complete_backups_are_restored(
    bkdb: BackupDb, keys: Keys, tools: PgTools, store_dir: Path
) -> None:
    backup(bkdb, keys, store_dir, tools)
    # A later run that died before its manifest was uploaded.
    late = store_dir / "20261023T020000Z"
    late.mkdir(parents=True)
    (late / DATABASE).write_bytes(b"partial")
    assert complete_backups(LocalStorage(store_dir)) == ["20261023T010000Z"]


def test_verification_catches_a_copy_without_force_rls_or_with_missing_rows(
    bkdb: BackupDb, keys: Keys, tools: PgTools, store_dir: Path
) -> None:
    pg = bkdb.pg
    backup(bkdb, keys, store_dir, tools)
    manifest = read_manifest(LocalStorage(store_dir), "20261023T010000Z", keys.secret)
    target = new_database_name("broken")
    try:
        assert restore(bkdb, keys, store_dir, tools, target).ok
        with psycopg.connect(pg.cluster.url(target), autocommit=True) as conn:
            conn.execute("ALTER TABLE velora.orders NO FORCE ROW LEVEL SECURITY")
            conn.execute("DROP POLICY tenant_isolation ON velora.customers")
            conn.execute("DELETE FROM velora.idempotency_keys")
        checks = {c.name: c for c in verify_restored(pg.cluster.admin_url, target, manifest)}
        assert not checks["FORCE RLS and policies"].ok
        assert "velora.orders" in checks["FORCE RLS and policies"].detail
        assert not checks["row counts"].ok
        assert "velora.idempotency_keys" in checks["row counts"].detail
        # Without its policy, app_rw sees none of the customers, not even its own.
        assert not checks["tenant isolation (as app_rw)"].ok
        assert "velora.customers" in checks["tenant isolation (as app_rw)"].detail
    finally:
        pg.cluster.drop_database(target)


def test_role_drift_is_reported() -> None:
    dumped = "\n".join(
        [
            "CREATE ROLE app_rw;",
            "ALTER ROLE app_rw WITH NOSUPERUSER NOINHERIT NOCREATEROLE NOCREATEDB LOGIN "
            "NOREPLICATION NOBYPASSRLS;",
            "ALTER ROLE postgres WITH SUPERUSER;",
        ]
    )
    expected = role_attributes(dumped)
    assert expected == {
        "app_rw": {
            "rolsuper": False,
            "rolinherit": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolcanlogin": True,
            "rolreplication": False,
            "rolbypassrls": False,
        }
    }


def test_role_check_against_the_cluster(bkdb: BackupDb) -> None:
    with psycopg.connect(bkdb.pg.cluster.admin_url) as conn:
        assert _check_roles(conn, {}).ok
        drift = _check_roles(conn, {"app_rw": {"rolcanlogin": False}})
    assert not drift.ok
    assert "app_rw.rolcanlogin is True, backup had False" in drift.detail
