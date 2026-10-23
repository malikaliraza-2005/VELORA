"""P12: `python -m velora.db provision` prepares a server for a deployment: the roles, their
passwords from the deployment's settings, and the database."""

from __future__ import annotations

import psycopg
import pytest

from tests.db.conftest import OWNER_ROLE, Cluster, new_database_name
from velora.db.__main__ import main
from velora.db.migrate import ROLE_URL_SETTINGS, MigrationError, migrate_up, provision


def _role_env(cluster: Cluster, dbname: str) -> dict[str, str]:
    return {name: cluster.url(dbname, role) for role, name in ROLE_URL_SETTINGS.items()}


def test_provision_creates_the_database_and_sets_every_password(cluster: Cluster) -> None:
    name = new_database_name("provision")
    try:
        # The session's own passwords, so the other tests' connections keep working.
        assert provision(cluster.admin_url, name, cluster.passwords) is True
        assert provision(cluster.admin_url, name, cluster.passwords) is False  # idempotent
        with psycopg.connect(cluster.url(name)) as conn:
            owner = conn.execute(
                "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s",
                (name,),
            ).fetchone()
        assert owner == (OWNER_ROLE,)
        migrate_up(cluster.url(name, OWNER_ROLE))
        for role in ROLE_URL_SETTINGS:
            with psycopg.connect(cluster.url(name, role)) as conn:
                assert conn.execute("SELECT current_user").fetchone() == (role,)
    finally:
        cluster.drop_database(name)


def test_only_velora_roles_get_passwords(cluster: Cluster) -> None:
    with pytest.raises(MigrationError):
        provision(cluster.admin_url, new_database_name("never"), {"postgres": "x"})


def test_command_line(
    cluster: Cluster, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = new_database_name("provcli")
    monkeypatch.setenv("VELORA_ADMIN_DATABASE_URL", cluster.admin_url)
    for key, url in _role_env(cluster, name).items():
        monkeypatch.setenv(key, url)
    try:
        assert main(["provision"]) == 0
        out = capsys.readouterr().out
        assert f"database {name} created" in out
        assert "passwords set for app_rw, velora_backup" in out
    finally:
        cluster.drop_database(name)


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [
        (
            "VELORA_DATABASE_URL",
            "postgresql://velora_owner:pw@db:5432/velora",
            "must connect as app_rw",
        ),
        ("VELORA_MONITOR_DATABASE_URL", "postgresql://velora_monitor@db:5432/velora", "password"),
        (
            "VELORA_BACKUP_DATABASE_URL",
            "postgresql://velora_backup:pw@db:5432/other",
            "names database 'other'",
        ),
    ],
)
def test_mismatched_settings_are_refused(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str, error: str
) -> None:
    monkeypatch.setenv("VELORA_ADMIN_DATABASE_URL", "postgresql://postgres:pw@db:5432/postgres")
    monkeypatch.setenv("VELORA_MIGRATION_DATABASE_URL", "postgresql://velora_owner:pw@db/velora")
    monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit, match=error):
        main(["provision"])
