"""Founder menu import (P4-T2): load a CSV into a tenant's draft menu.

Runs as `velora_platform`, which may fill a draft but can't publish it: the owner reviews
the draft on the dashboard and publishes it there (AD-28). The import replaces the whole
draft and is audited with the operator's name, the file's SHA-256 and its price changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import psycopg

from velora.clock import Clock, SystemClock
from velora.db import TenantId
from velora.menu import store
from velora.menu.csv_import import parse_menu_csv
from velora.menu.store import Editor


@dataclass(frozen=True)
class ImportResult:
    version_no: int
    revision: int
    items: int


async def import_menu(
    url: str,
    tenant: TenantId,
    data: bytes,
    *,
    operator: str,
    clock: Clock | None = None,
) -> ImportResult:
    """Parse `data` (raises CsvImportError with every problem) and save it as the draft."""
    menu = parse_menu_csv(data)
    now = (clock or SystemClock()).now()
    async with await psycopg.AsyncConnection.connect(url) as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_catalog.set_config('app.tenant_id', %s, true)", (str(tenant.value),)
        )
        version = await store.save_draft(
            conn,
            tenant,
            menu,
            editor=Editor("platform", operator=operator),
            base_revision=None,
            now=now,
            action="menu.draft_imported",
            details={"source": "csv", "sha256": hashlib.sha256(data).hexdigest()},
        )
    return ImportResult(version.version_no, version.revision, menu.item_count())
