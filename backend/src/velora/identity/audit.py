"""Audit log writes (P3-T6). Every privileged action records actor, tenant, action and time.

The row is written in the same transaction as the action, so an action without its audit row
can't commit. `audit_log` is append-only: no role may update or delete it (migration 0005).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from velora.db import TenantId

ActorKind = Literal["customer", "staff", "system", "platform"]


async def record(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    *,
    at: datetime,
    actor_kind: ActorKind,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO velora.audit_log "
        "(tenant_id, actor_kind, actor_id, action, target_type, target_id, details, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            tenant.value,
            actor_kind,
            actor_id,
            action,
            target_type,
            target_id,
            Jsonb(details or {}),
            at,
        ),
    )
