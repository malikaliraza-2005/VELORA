"""The deployment's settings files (P12-T3).

A deployment keeps its settings in one directory (`/etc/velora` on the server), one file per
container, so each container gets only the secrets it needs:

    stack.env    compose variables: the hostname and TLS mode (no secrets)
    db.env       PostgreSQL's administrator password
    api.env      the API and dashboard: app_rw
    worker.env   the worker: app_rw, velora_dispatcher, the Groq key
    monitor.env  the monitor: velora_monitor, alert delivery, heartbeat
    backup.env   the backup job: velora_backup, the PUBLIC backup key, storage
    ops.env      the founder's tools: the administrator and every role, for provisioning,
                 migrations, onboarding and restores

`write_initial` makes them with fresh random passwords; `check` verifies a directory before
every release: owner-only permissions, no placeholders where a value is required, settings
that the services accept, and the same password for a role in every file.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from psycopg.conninfo import conninfo_to_dict

from velora.backup.config import BackupSettings
from velora.config import PLACEHOLDER, Settings
from velora.db.migrate import ROLE_URL_SETTINGS
from velora.monitoring.config import MonitorSettings

DATABASE = "velora"
DB_HOST = "db"
DB_PORT = 5432
FILES = ("stack.env", "db.env", "api.env", "worker.env", "monitor.env", "backup.env", "ops.env")
# Settings a deployment can't run without. Anything else may stay empty or CHANGE_ME.
REQUIRED: Mapping[str, tuple[str, ...]] = {
    "stack.env": ("VELORA_DOMAIN", "VELORA_TLS"),
    "db.env": ("POSTGRES_PASSWORD",),
    "api.env": ("VELORA_ENV", "VELORA_PUBLIC_BASE_URL", "VELORA_DATABASE_URL"),
    "worker.env": (
        "VELORA_ENV",
        "VELORA_PUBLIC_BASE_URL",
        "VELORA_DATABASE_URL",
        "VELORA_DISPATCHER_DATABASE_URL",
    ),
    "monitor.env": ("VELORA_ENV", "VELORA_MONITOR_DATABASE_URL", "VELORA_MONITOR_PROBE_URL"),
    "backup.env": (
        "VELORA_ENV",
        "VELORA_BACKUP_DATABASE_URL",
        "VELORA_BACKUP_PUBLIC_KEY",
        "VELORA_BACKUP_STORAGE",
        "VELORA_MONITOR_DATABASE_URL",
    ),
    "ops.env": ("VELORA_ENV", "VELORA_ADMIN_DATABASE_URL", *ROLE_URL_SETTINGS.values()),
}
_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9-]{1,63}$")

Environment = Literal["staging", "production"]


@dataclass(frozen=True)
class StackChoices:
    env: Environment
    domain: str
    # "acme": a public certificate (Let's Encrypt, over port 443 only). "internal": Caddy's
    # own authority, for a stack that isn't reachable from the internet (CI rehearsals).
    tls: Literal["acme", "internal"] = "acme"
    alert_email: str | None = None
    alert_webhook: str | None = None


def _url(role: str, password: str) -> str:
    return f"postgresql://{role}:{quote(password, safe='')}@{DB_HOST}:{DB_PORT}/{DATABASE}"


def _render(lines: list[tuple[str, str] | str]) -> str:
    out = []
    for line in lines:
        out.append(f"# {line}" if isinstance(line, str) else f"{line[0]}={line[1]}")
    return "\n".join(out) + "\n"


def initial_files(
    choices: StackChoices, token: Callable[[], str] = lambda: secrets.token_urlsafe(32)
) -> dict[str, str]:
    """The contents of every settings file for a new deployment, with fresh passwords."""
    if not _HOSTNAME.match(choices.domain):
        raise ValueError(f"not a hostname: {choices.domain!r}")
    passwords = {role: token() for role in ROLE_URL_SETTINGS}
    urls = {role: _url(role, pw) for role, pw in passwords.items()}
    admin = f"postgresql://postgres:{quote(token(), safe='')}@{DB_HOST}:{DB_PORT}/postgres"
    admin_password = str(conninfo_to_dict(admin)["password"])
    base = [
        ("VELORA_ENV", choices.env),
        ("VELORA_LOG_LEVEL", "INFO"),
        ("VELORA_LOG_FORMAT", "json"),
    ]
    origin = ("VELORA_PUBLIC_BASE_URL", f"https://{choices.domain}")
    header = "Written by `velora env-init`. Owner-only (0600). Never copy into the repository."
    return {
        "stack.env": _render(
            [
                header,
                "The dashboard's hostname; DNS must point at this server.",
                ("VELORA_DOMAIN", choices.domain),
                "acme: a public certificate over port 443. internal: test stacks only.",
                ("VELORA_TLS", choices.tls),
            ]
        ),
        "db.env": _render([header, ("POSTGRES_PASSWORD", admin_password)]),
        "api.env": _render([header, *base, origin, ("VELORA_DATABASE_URL", urls["app_rw"])]),
        "worker.env": _render(
            [
                header,
                *base,
                origin,
                ("VELORA_DATABASE_URL", urls["app_rw"]),
                ("VELORA_DISPATCHER_DATABASE_URL", urls["velora_dispatcher"]),
                "Groq free tier (AD-35). Empty: customers use the numbered menu only.",
                ("VELORA_GROQ_API_KEY", ""),
                ("VELORA_AI_MODEL", ""),
                ("VELORA_AI_TIMEOUT_SECONDS", ""),
            ]
        ),
        "monitor.env": _render(
            [
                header,
                *base,
                ("VELORA_MONITOR_DATABASE_URL", urls["velora_monitor"]),
                ("VELORA_MONITOR_PROBE_URL", "http://api:8000/readyz"),
                "A free dead man's switch (AD-35): pinged every minute.",
                ("VELORA_MONITOR_HEARTBEAT_URL", ""),
                "At least one way to reach the founder (never WhatsApp).",
                ("VELORA_ALERT_WEBHOOK_URL", choices.alert_webhook or ""),
                ("VELORA_ALERT_EMAIL_TO", choices.alert_email or ""),
                ("VELORA_SMTP_HOST", PLACEHOLDER if choices.alert_email else ""),
                ("VELORA_SMTP_PORT", ""),
                ("VELORA_SMTP_FROM", PLACEHOLDER if choices.alert_email else ""),
                ("VELORA_SMTP_USERNAME", ""),
                ("VELORA_SMTP_PASSWORD", ""),
                ("VELORA_MONITOR_EXPECT_BACKUPS", "true"),
            ]
        ),
        "backup.env": _render(
            [
                header,
                *base,
                ("VELORA_BACKUP_DATABASE_URL", urls["velora_backup"]),
                ("VELORA_MONITOR_DATABASE_URL", urls["velora_monitor"]),
                "The PUBLIC key from `python -m velora.backup keygen`, made on YOUR computer.",
                ("VELORA_BACKUP_PUBLIC_KEY", PLACEHOLDER),
                "s3://bucket/prefix (free S3-compatible storage, B5), or file:///backups.",
                ("VELORA_BACKUP_STORAGE", PLACEHOLDER),
                ("VELORA_BACKUP_S3_ENDPOINT", ""),
                ("VELORA_BACKUP_S3_REGION", ""),
                "A key that can write but not delete; a lifecycle rule removes old backups.",
                ("VELORA_BACKUP_S3_ACCESS_KEY_ID", ""),
                ("VELORA_BACKUP_S3_SECRET_ACCESS_KEY", ""),
                ("VELORA_BACKUP_WORK_DIR", "/work"),
            ]
        ),
        "ops.env": _render(
            [
                header,
                *base,
                origin,
                ("VELORA_ADMIN_DATABASE_URL", admin),
                *((ROLE_URL_SETTINGS[role], url) for role, url in urls.items()),
                "Your name, recorded in the audit log by `velora admin`.",
                ("VELORA_OPERATOR", ""),
            ]
        ),
    }


def write_initial(directory: Path, files: Mapping[str, str]) -> list[Path]:
    """Create the files, owner-only. Refuses to overwrite: passwords already set on the
    database would no longer match."""
    existing = [directory / name for name in files if (directory / name).exists()]
    if existing:
        raise FileExistsError(f"already exists: {', '.join(str(p) for p in existing)}")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    written = []
    for name, content in files.items():
        path = directory / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content.encode("utf-8"))
        written.append(path)
    return written


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"not KEY=value: {line[:40]!r}")
        values[key.strip()] = value.strip()
    return values


def _permission_problems(directory: Path, paths: list[Path]) -> list[str]:
    if sys.platform == "win32":
        return []
    problems = []
    for path in [directory, *paths]:
        info = path.stat()
        loose = stat.S_IMODE(info.st_mode) & 0o077
        if loose:
            problems.append(f"{path}: readable or writable by others (mode {info.st_mode:o})")
        if info.st_uid != os.geteuid():
            problems.append(f"{path}: owned by uid {info.st_uid}, not {os.geteuid()}")
    return problems


def _service_problems(name: str, values: Mapping[str, str]) -> list[str]:
    loaders: dict[str, Callable[[Mapping[str, str]], object]] = {
        "api.env": Settings.from_env,
        "worker.env": Settings.from_env,
        "ops.env": Settings.from_env,
        "monitor.env": MonitorSettings.from_env,
        "backup.env": BackupSettings.from_env,
    }
    loader = loaders.get(name)
    if loader is None:
        return []
    try:
        loader(values)
    except ValueError as exc:
        return [f"{name}: {exc}"]
    return []


def check(directory: Path) -> list[str]:
    """Everything wrong with a deployment's settings directory; empty when it's ready."""
    paths = [directory / name for name in FILES]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        return [f"missing: {p}" for p in missing]
    problems = _permission_problems(directory, paths)
    files = {p.name: parse(p.read_text(encoding="utf-8")) for p in paths}
    for name, keys in REQUIRED.items():
        for key in keys:
            if files[name].get(key, "") in {"", PLACEHOLDER}:
                problems.append(f"{name}: {key} is not set")
    for name, values in files.items():
        problems += _service_problems(name, values)
    stack = files["stack.env"]
    if stack.get("VELORA_TLS") not in {"acme", "internal"}:
        problems.append("stack.env: VELORA_TLS must be acme or internal")
    domain = stack.get("VELORA_DOMAIN", "")
    envs = {files[n].get("VELORA_ENV") for n in FILES if n not in {"stack.env", "db.env"}}
    if len(envs) != 1 or not envs <= {"staging", "production"}:
        problems.append(f"every file must name the same VELORA_ENV, staging or production: {envs}")
    for name in ("api.env", "worker.env", "ops.env"):
        if files[name].get("VELORA_PUBLIC_BASE_URL") != f"https://{domain}":
            problems.append(f"{name}: VELORA_PUBLIC_BASE_URL must be https://{domain}")
    if envs == {"production"} and stack.get("VELORA_TLS") != "acme":
        problems.append("stack.env: production needs VELORA_TLS=acme")
    problems += _consistency_problems(files)
    return problems


def _consistency_problems(files: Mapping[str, Mapping[str, str]]) -> list[str]:
    """A role's connection must be the same wherever it appears, and must be the one that
    `provision` sets on the database (ops.env)."""
    problems = []
    for role, key in ROLE_URL_SETTINGS.items():
        reference = files["ops.env"].get(key, "")
        if reference in {"", PLACEHOLDER}:
            continue
        params = conninfo_to_dict(reference)
        if params.get("user") != role or params.get("dbname") != DATABASE:
            problems.append(f"ops.env: {key} must connect to {DATABASE} as {role}")
        for name, values in files.items():
            if name != "ops.env" and values.get(key, reference) != reference:
                problems.append(f"{name}: {key} differs from ops.env")
    admin = files["ops.env"].get("VELORA_ADMIN_DATABASE_URL", "")
    if admin not in {"", PLACEHOLDER}:
        password = conninfo_to_dict(admin).get("password")
        if password != files["db.env"].get("POSTGRES_PASSWORD"):
            problems.append("ops.env: VELORA_ADMIN_DATABASE_URL's password differs from db.env")
    return problems
