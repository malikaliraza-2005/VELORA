"""The deployment snapshot (P10-T2): every metric the alerts and the founder's status use.

Read as `velora_monitor` in one read-only transaction. The role can see statuses, timestamps,
ids and tenant names across tenants, and nothing else (migration 0012).

Queue age is how long the oldest item has been due and not taken: a stopped worker shows up
as a growing age. Dead letters are inbox events that ended `failed`, and outbox messages that
ended `failed` or `unknown` (never re-sent automatically), in the last 24 hours.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from psycopg import AsyncConnection

from velora.conversation.pktime import PKT
from velora.identity.errors import expect_row

DEAD_LETTER_WINDOW = timedelta(hours=24)
INGRESS_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True)
class ChannelHealth:
    tenant_id: uuid.UUID
    tenant: str
    channel_id: uuid.UUID
    state: str
    # Since when the channel has been in this state, if known.
    since: datetime | None


@dataclass(frozen=True)
class BackupRun:
    ok: bool
    kind: str
    at: datetime
    detail: str | None


@dataclass(frozen=True)
class Funnel:
    """Today's orders (Pakistan day, all restaurants), by where they are now."""

    placed: int = 0
    awaiting: int = 0
    in_progress: int = 0
    completed: int = 0
    cancelled: int = 0
    expired: int = 0
    refund_pending: int = 0


@dataclass(frozen=True)
class Snapshot:
    at: datetime
    global_pause: str | None = None
    inbox_due: int = 0
    inbox_oldest_due: timedelta | None = None
    inbox_failed: int = 0
    outbox_due: int = 0
    outbox_oldest_due: timedelta | None = None
    outbox_waiting_for_channel: int = 0
    outbox_failed: int = 0
    outbox_unknown: int = 0
    channels: tuple[ChannelHealth, ...] = ()
    channel_changes: int = 0
    # Refused webhooks in the last 15 minutes, by reason.
    ingress_rejections: dict[str, int] = field(default_factory=dict)
    last_backup: BackupRun | None = None
    last_good_backup: datetime | None = None
    # The worker's last heartbeat (P11, every 15 seconds); None if it never ran.
    worker_last_seen: datetime | None = None
    funnel: Funnel = field(default_factory=Funnel)

    @property
    def dead_letters(self) -> int:
        return self.inbox_failed + self.outbox_failed + self.outbox_unknown


def pk_day_bounds(day: date) -> tuple[datetime, datetime]:
    starts = datetime.combine(day, time(0), PKT)
    return starts, starts + timedelta(days=1)


async def collect(conn: AsyncConnection[Any], now: datetime) -> Snapshot:
    """Read every metric. `conn` is a `velora_monitor` connection."""
    async with conn.transaction():
        await conn.execute("SET TRANSACTION READ ONLY")
        cur = await conn.execute(
            "SELECT paused, reason FROM velora.global_pause_events ORDER BY seq DESC LIMIT 1"
        )
        pause = await cur.fetchone()
        cur = await conn.execute(
            """
            SELECT count(*) FILTER (WHERE due),
                   min(available_at) FILTER (WHERE due),
                   count(*) FILTER (WHERE status = 'failed' AND available_at > %(dead)s)
              FROM (SELECT status, available_at,
                           (status = 'pending' AND available_at <= %(now)s)
                           OR status = 'processing' AS due
                      FROM velora.inbox_events
                     WHERE status IN ('pending', 'processing', 'failed')) e
            """,
            {"now": now, "dead": now - DEAD_LETTER_WINDOW},
        )
        inbox_due, inbox_since, inbox_failed = expect_row(await cur.fetchone())
        cur = await conn.execute(
            """
            SELECT count(*) FILTER (WHERE status = 'pending' AND next_attempt_at <= %(now)s),
                   min(next_attempt_at) FILTER (WHERE status = 'pending'
                                                AND next_attempt_at <= %(now)s),
                   count(*) FILTER (WHERE status = 'waiting_for_channel'),
                   count(*) FILTER (WHERE status = 'failed'
                                    AND greatest(created_at, next_attempt_at) > %(dead)s),
                   count(*) FILTER (WHERE status = 'unknown'
                                    AND greatest(created_at, next_attempt_at) > %(dead)s)
              FROM velora.outbox_messages
             WHERE status IN ('pending', 'waiting_for_channel', 'failed', 'unknown')
            """,
            {"now": now, "dead": now - DEAD_LETTER_WINDOW},
        )
        outbox_due, outbox_since, waiting, outbox_failed, outbox_unknown = expect_row(
            await cur.fetchone()
        )
        cur = await conn.execute(
            "SELECT c.tenant_id, t.slug, c.id, c.state, c.state_changed_at "
            "FROM velora.channels c JOIN velora.tenants t ON t.id = c.tenant_id "
            "WHERE c.state <> 'retired' ORDER BY t.slug, c.id"
        )
        channels = tuple(ChannelHealth(*row) for row in await cur.fetchall())
        cur = await conn.execute(
            "SELECT count(*) FROM velora.channel_events "
            "WHERE event_type = 'state_changed' AND created_at > %s",
            (now - DEAD_LETTER_WINDOW,),
        )
        (changes,) = expect_row(await cur.fetchone())
        cur = await conn.execute(
            "SELECT reason, count(*) FROM velora.ingress_rejections WHERE created_at > %s "
            "GROUP BY reason ORDER BY reason",
            (now - INGRESS_WINDOW,),
        )
        rejections = {reason: int(n) for reason, n in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT ok, kind, created_at, detail FROM velora.backup_runs ORDER BY seq DESC LIMIT 1"
        )
        last = await cur.fetchone()
        cur = await conn.execute("SELECT max(created_at) FROM velora.backup_runs WHERE ok")
        (last_good,) = expect_row(await cur.fetchone())
        cur = await conn.execute("SELECT max(last_seen_at) FROM velora.worker_heartbeats")
        (worker_seen,) = expect_row(await cur.fetchone())
        starts, ends = pk_day_bounds(now.astimezone(PKT).date())
        cur = await conn.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE status IN ('cod_confirmed', 'awaiting_transfer',
                                                     'paid_staff_verified')),
                   count(*) FILTER (WHERE status IN ('accepted', 'preparing',
                                                     'out_for_delivery', 'ready')),
                   count(*) FILTER (WHERE status = 'completed'),
                   count(*) FILTER (WHERE status IN ('cancelled', 'rejected')),
                   count(*) FILTER (WHERE status = 'expired'),
                   count(*) FILTER (WHERE status = 'refund_pending')
              FROM velora.orders
             WHERE created_at >= %s AND created_at < %s
               AND status NOT IN ('draft', 'awaiting_confirmation')
            """,
            (starts, ends),
        )
        funnel = Funnel(*(int(n) for n in expect_row(await cur.fetchone())))
    return Snapshot(
        at=now,
        global_pause=(pause[1] or "paused") if pause is not None and pause[0] else None,
        inbox_due=int(inbox_due),
        inbox_oldest_due=None if inbox_since is None else max(now - inbox_since, timedelta(0)),
        inbox_failed=int(inbox_failed),
        outbox_due=int(outbox_due),
        outbox_oldest_due=None if outbox_since is None else max(now - outbox_since, timedelta(0)),
        outbox_waiting_for_channel=int(waiting),
        outbox_failed=int(outbox_failed),
        outbox_unknown=int(outbox_unknown),
        channels=channels,
        channel_changes=int(changes),
        ingress_rejections=rejections,
        last_backup=None if last is None else BackupRun(*last),
        last_good_backup=last_good,
        worker_last_seen=worker_seen,
        funnel=funnel,
    )


async def record_backup(
    conn: AsyncConnection[Any], *, ok: bool, kind: str, detail: str | None, at: datetime
) -> None:
    """Called by the backup job (P11) after each run, as `velora_monitor`."""
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO velora.backup_runs (ok, kind, detail, created_at) VALUES (%s, %s, %s, %s)",
            (ok, kind, detail, at),
        )
