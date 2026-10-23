"""Backup settings, from environment variables (P11).

Real values live only in the environment; `.env.example` holds placeholders. The backup job
needs the backup role's connection, the **public** key and a storage location. A restore
also needs the secret key, which is never kept on the server: it is given as a file path on
the command line at restore time.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from psycopg.conninfo import conninfo_to_dict

from velora.backup.storage import LocalStorage, S3Settings, S3Storage, Storage, StorageError
from velora.clock import Clock
from velora.config import PLACEHOLDER


def _optional(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name, "").strip()
    return None if not value or value == PLACEHOLDER else value


@dataclass(frozen=True)
class PgTools:
    """How to run pg_dump, pg_dumpall and pg_restore.

    `prefix` runs them elsewhere, e.g. `docker exec -i -e PGPASSWORD <container>` when the
    client tools live in the database container; then `address` is the server as seen from
    there (usually 127.0.0.1:5432). The password always travels in PGPASSWORD, never on the
    command line, so it doesn't show in the process list.
    """

    prefix: tuple[str, ...] = ()
    address: tuple[str, int] | None = None

    def command(self, tool: str, url: str, *args: str) -> tuple[list[str], dict[str, str]]:
        params: dict[str, Any] = conninfo_to_dict(url)
        host, port = self.address or (
            str(params.get("host", "localhost")),
            int(params.get("port", 5432)),
        )
        argv = [
            *self.prefix,
            tool,
            "--host",
            host,
            "--port",
            str(port),
            "--username",
            str(params.get("user", "postgres")),
            "--no-password",
            *args,
        ]
        env = dict(os.environ)
        env.pop("PGPASSWORD", None)
        if params.get("password"):
            env["PGPASSWORD"] = str(params["password"])
        return argv, env

    @classmethod
    def from_text(cls, prefix: str | None, address: str | None) -> PgTools:
        parsed: tuple[str, int] | None = None
        if address is not None:
            host, sep, port = address.rpartition(":")
            if not sep or not host or not port.isdigit():
                raise ValueError("VELORA_BACKUP_PG_TOOLS_ADDRESS must be host:port")
            parsed = (host, int(port))
        return cls(tuple(shlex.split(prefix or "")), parsed)


@dataclass(frozen=True)
class BackupSettings:
    env: str
    # velora_backup: reads everything, writes nothing (roles.sql).
    database_url: str | None = field(default=None, repr=False)
    public_key: str | None = None
    # file:///path or s3://bucket/prefix
    storage_url: str | None = None
    s3_endpoint: str | None = None
    s3_region: str | None = None
    s3_access_key: str | None = field(default=None, repr=False)
    s3_secret_key: str | None = field(default=None, repr=False)
    tools: PgTools = PgTools()
    # velora_monitor: each run is reported for the P10 backup alerts.
    monitor_database_url: str | None = field(default=None, repr=False)
    # Where encrypted files wait for upload. Never holds plaintext.
    work_dir: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> BackupSettings:
        source = os.environ if environ is None else environ
        env = source.get("VELORA_ENV", "development").strip().lower()
        work_dir = _optional(source, "VELORA_BACKUP_WORK_DIR")
        endpoint = _optional(source, "VELORA_BACKUP_S3_ENDPOINT")
        if (
            endpoint is not None
            and env in {"staging", "production"}
            and urlsplit(endpoint).scheme != "https"
        ):
            raise ValueError("VELORA_BACKUP_S3_ENDPOINT must use https in staging and production")
        return cls(
            env=env,
            database_url=_optional(source, "VELORA_BACKUP_DATABASE_URL"),
            public_key=_optional(source, "VELORA_BACKUP_PUBLIC_KEY"),
            storage_url=_optional(source, "VELORA_BACKUP_STORAGE"),
            s3_endpoint=endpoint,
            s3_region=_optional(source, "VELORA_BACKUP_S3_REGION"),
            s3_access_key=_optional(source, "VELORA_BACKUP_S3_ACCESS_KEY_ID"),
            s3_secret_key=_optional(source, "VELORA_BACKUP_S3_SECRET_ACCESS_KEY"),
            tools=PgTools.from_text(
                _optional(source, "VELORA_BACKUP_PG_TOOLS"),
                _optional(source, "VELORA_BACKUP_PG_TOOLS_ADDRESS"),
            ),
            monitor_database_url=_optional(source, "VELORA_MONITOR_DATABASE_URL"),
            work_dir=None if work_dir is None else Path(work_dir),
        )

    def storage(self, clock: Clock) -> Storage:
        return open_storage(
            self.storage_url,
            clock,
            endpoint=self.s3_endpoint,
            region=self.s3_region,
            access_key=self.s3_access_key,
            secret_key=self.s3_secret_key,
        )


def open_storage(
    url: str | None,
    clock: Clock,
    *,
    endpoint: str | None = None,
    region: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
) -> Storage:
    if url is None:
        raise StorageError("VELORA_BACKUP_STORAGE is not set")
    parts = urlsplit(url)
    if parts.scheme == "file":
        path = unquote(parts.path)
        # file:///C:/backups on Windows
        if len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return LocalStorage(Path(path))
    if parts.scheme == "s3":
        if not (parts.netloc and endpoint and region and access_key and secret_key):
            raise StorageError(
                "s3 storage needs a bucket, and VELORA_BACKUP_S3_ENDPOINT, _REGION, "
                "_ACCESS_KEY_ID and _SECRET_ACCESS_KEY"
            )
        return S3Storage(
            S3Settings(
                endpoint, region, parts.netloc, parts.path.strip("/"), access_key, secret_key
            ),
            clock,
        )
    raise StorageError("VELORA_BACKUP_STORAGE must be file:///path or s3://bucket/prefix")
