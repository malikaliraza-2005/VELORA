"""P12-T3: the deployment's settings files: generated owner-only, and checked before every
release."""

from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from velora.deploy.__main__ import main
from velora.deploy.envfiles import (
    FILES,
    StackChoices,
    check,
    initial_files,
    parse,
    write_initial,
)

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
PUBLIC_KEY = "velora-backup-public-1:Nx-uhEkpnd3iYR7ndweL39IsUkB-fszga4fuiN1MfHw"


def _ready(directory: Path, env: str = "staging") -> None:
    """A complete, valid deployment: the generated files plus the founder's own values."""
    choices = StackChoices(env=env, domain="dash.example.com", alert_webhook="https://hook.ex/a")
    write_initial(directory, initial_files(choices))
    _set(directory, "backup.env", VELORA_BACKUP_PUBLIC_KEY=PUBLIC_KEY)
    _set(directory, "backup.env", VELORA_BACKUP_STORAGE="file:///backups")


def _set(directory: Path, name: str, **values: str) -> None:
    path = directory / name
    lines = path.read_text(encoding="utf-8").splitlines()
    for key, value in values.items():
        lines = [f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines]
        if not any(line.startswith(f"{key}=") for line in lines):
            lines.append(f"{key}={value}")
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def test_every_role_gets_its_own_fresh_password() -> None:
    counter = itertools.count()
    files = initial_files(
        StackChoices(env="staging", domain="dash.example.com"), token=lambda: f"pw{next(counter)}"
    )
    ops = parse(files["ops.env"])
    passwords = {
        str(conninfo_to_dict(value)["user"]): conninfo_to_dict(value)["password"]
        for key, value in ops.items()
        if key.endswith("DATABASE_URL")
    }
    assert len(passwords) == 7  # six VELORA roles and the administrator
    assert len(set(passwords.values())) == 7
    assert parse(files["db.env"])["POSTGRES_PASSWORD"] == passwords["postgres"]


def test_each_container_gets_only_its_own_secrets() -> None:
    files = {
        name: parse(text)
        for name, text in initial_files(StackChoices("staging", "dash.example.com")).items()
    }
    users = {
        name: {
            str(conninfo_to_dict(v)["user"])
            for k, v in values.items()
            if k.endswith("DATABASE_URL") and v
        }
        for name, values in files.items()
    }
    assert users["api.env"] == {"app_rw"}
    assert users["worker.env"] == {"app_rw", "velora_dispatcher"}
    assert users["monitor.env"] == {"velora_monitor"}
    assert users["backup.env"] == {"velora_backup", "velora_monitor"}
    assert "POSTGRES_PASSWORD" not in files["api.env"]
    assert all("GROQ" not in key for key in files["api.env"])


def test_a_bad_hostname_is_refused() -> None:
    with pytest.raises(ValueError):
        initial_files(StackChoices("staging", "https://dash.example.com"))


@POSIX_ONLY
def test_files_are_written_owner_only_and_never_overwritten(tmp_path: Path) -> None:
    directory = tmp_path / "velora"
    written = write_initial(directory, initial_files(StackChoices("staging", "d.example.com")))
    assert sorted(p.name for p in written) == sorted(FILES)
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in written)
    with pytest.raises(FileExistsError):
        write_initial(directory, initial_files(StackChoices("staging", "d.example.com")))


def test_a_complete_deployment_passes(tmp_path: Path) -> None:
    _ready(tmp_path / "etc")
    assert check(tmp_path / "etc") == []


def test_placeholders_and_missing_values_are_reported(tmp_path: Path) -> None:
    directory = tmp_path / "etc"
    write_initial(directory, initial_files(StackChoices("staging", "dash.example.com")))
    problems = check(directory)
    assert "backup.env: VELORA_BACKUP_PUBLIC_KEY is not set" in problems
    assert "backup.env: VELORA_BACKUP_STORAGE is not set" in problems
    # The monitor refuses a deployment with no way to reach the founder.
    assert any(p.startswith("monitor.env: set VELORA_ALERT_EMAIL_TO") for p in problems)


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    _ready(tmp_path / "etc")
    (tmp_path / "etc" / "worker.env").unlink()
    assert check(tmp_path / "etc") == [f"missing: {tmp_path / 'etc' / 'worker.env'}"]


@pytest.mark.parametrize(
    ("name", "values", "problem"),
    [
        (
            "api.env",
            {"VELORA_DATABASE_URL": "postgresql://app_rw:other@db:5432/velora"},
            "api.env: VELORA_DATABASE_URL differs from ops.env",
        ),
        (
            "db.env",
            {"POSTGRES_PASSWORD": "changed"},
            "ops.env: VELORA_ADMIN_DATABASE_URL's password differs from db.env",
        ),
        (
            "worker.env",
            {"VELORA_PUBLIC_BASE_URL": "https://elsewhere.example.com"},
            "worker.env: VELORA_PUBLIC_BASE_URL must be https://dash.example.com",
        ),
        (
            "ops.env",
            {"VELORA_DATABASE_URL": "postgresql://velora_owner:x@db:5432/velora"},
            "ops.env: VELORA_DATABASE_URL must connect to velora as app_rw",
        ),
        (
            "monitor.env",
            {"VELORA_ENV": "production"},
            "every file must name the same VELORA_ENV",
        ),
        (
            "monitor.env",
            {"VELORA_ALERT_WEBHOOK_URL": "http://hook.example.com/a"},
            "monitor.env: VELORA_ALERT_WEBHOOK_URL must use https in staging and production",
        ),
        ("stack.env", {"VELORA_TLS": "none"}, "stack.env: VELORA_TLS must be acme or internal"),
    ],
)
def test_inconsistent_settings_are_reported(
    tmp_path: Path, name: str, values: dict[str, str], problem: str
) -> None:
    _ready(tmp_path / "etc")
    _set(tmp_path / "etc", name, **values)
    assert any(p.startswith(problem) for p in check(tmp_path / "etc")), check(tmp_path / "etc")


def test_production_needs_a_public_certificate(tmp_path: Path) -> None:
    _ready(tmp_path / "etc", env="production")
    assert check(tmp_path / "etc") == []
    _set(tmp_path / "etc", "stack.env", VELORA_TLS="internal")
    assert check(tmp_path / "etc") == ["stack.env: production needs VELORA_TLS=acme"]


@POSIX_ONLY
def test_files_readable_by_others_are_reported(tmp_path: Path) -> None:
    _ready(tmp_path / "etc")
    os.chmod(tmp_path / "etc" / "worker.env", 0o644)
    os.chmod(tmp_path / "etc", 0o755)  # noqa: S103 - the problem under test
    problems = check(tmp_path / "etc")
    assert len(problems) == 2
    assert all("readable or writable by others" in p for p in problems)


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    directory = tmp_path / "etc"
    args = ["--dir", str(directory), "--env", "staging", "--domain", "Dash.Example.com"]
    assert main(["env-init", *args, "--alert-webhook", "https://hook.ex/a"]) == 0
    assert main(["env-init", *args]) == 2  # never overwrites
    assert main(["env-check", "--dir", str(directory)]) == 1
    assert "VELORA_BACKUP_PUBLIC_KEY is not set" in capsys.readouterr().err
    _set(directory, "backup.env", VELORA_BACKUP_PUBLIC_KEY=PUBLIC_KEY)
    _set(directory, "backup.env", VELORA_BACKUP_STORAGE="file:///backups")
    assert main(["env-check", "--dir", str(directory)]) == 0
    assert parse((directory / "stack.env").read_text(encoding="utf-8"))["VELORA_DOMAIN"] == (
        "dash.example.com"
    )
