"""Recovery mode on startup (P11-T4, AD-21).

Every start of the worker runs recovery before any normal work. The steps follow the P11
plan, in order:

1. **Database health check:** the database answers, and has the tables this code needs (the
   runtime role can't read the migration ledger, by design). Otherwise the worker doesn't
   start.
2. **Reclaim leases:** inbox events a dead worker was processing go back to `pending`.
3. **Reconcile inbound (V-04):** fetch messages the provider holds but we never stored. How
   is decided by the OpenWA trial, so until then it is a hook, recorded as `skipped`.
4. **Void pending YES nonces** issued before the outage; each customer whose summary was
   still valid is asked to confirm again (`velora.orders.recovery`, P7-T7).
5. **Extend transfer windows** still open when the outage began, by the outage's length.
6. **Mark in-flight sends `unknown`:** a message the dead worker was sending may or may not
   have reached the customer, so it is never re-sent automatically (V-06).
7. **Replace stale replies** with one "we're back" message per customer.
8. **Rate-limited flush:** recovery sends nothing itself. The normal sender then flushes the
   held messages in order, under its rate cap.
9. **Highlight orders placed during recovery:** each run is recorded in `recovery_runs`, and
   orders placed before the backlog of held customer messages is cleared are flagged on the
   dashboard (`RecoveryTracker`).

Steps 2 to 7 run for each tenant in **one transaction**, together with its `recovery_runs`
row, keyed by the outage. A recovery that is interrupted is simply run again on the next
start, and a tenant already recovered from this outage is skipped, so no window is extended
twice.

**When did the outage begin?** The worker records a heartbeat (`worker_heartbeats`) every
15 seconds and when it stops. The outage is taken to begin one interval after the last
heartbeat: the worker may have been alive, issuing nonces, until then. A first start has no
heartbeat, so steps 4 and 5 have nothing to compare against and are skipped.

**One worker at a time.** Steps 2 and 6 assume no other worker is running. The worker holds a
session advisory lock for its whole life (`hold_worker_lock`), and a second one refuses to
start (AD-19: one worker).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection

from velora.clock import Clock
from velora.db import Database, TenantId
from velora.db.access import ConnectionPool
from velora.identity.errors import expect_row
from velora.messaging.dispatch import Dispatcher
from velora.messaging.outbox import replace_stale
from velora.orders.recovery import void_pre_outage
from velora.redaction import error_text

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = timedelta(seconds=15)
WORKER_NAME = "worker"
# Session advisory lock held by the running worker ("velora-w").
WORKER_LOCK_KEY = 0x76656C6F72612D77

# Step 3: (connection in the tenant's transaction, tenant, outage start, now) -> events stored.
InboundReconciler = Callable[[AsyncConnection[Any], TenantId, datetime, datetime], Awaitable[int]]


class RecoveryError(RuntimeError):
    """Recovery could not finish. The worker must not start normal work."""


class WorkerAlreadyRunningError(RecoveryError):
    """Another worker holds the lock."""


@dataclass(frozen=True)
class TenantRecovery:
    tenant: TenantId
    leases_reclaimed: int
    inbound: Literal["skipped", "reconciled"]
    inbound_recovered: int
    nonces_voided: int
    windows_extended: int
    messages_unknown: int
    replies_stale: int
    backlog: bool


@dataclass
class RecoveryReport:
    outage_started_at: datetime | None
    started_at: datetime
    tenants: list[TenantRecovery] = field(default_factory=list)
    # Tenants already recovered from this outage by an earlier, interrupted run.
    already_done: list[TenantId] = field(default_factory=list)

    @property
    def outage(self) -> timedelta | None:
        if self.outage_started_at is None:
            return None
        return self.started_at - self.outage_started_at


class Heartbeat:
    """The worker's sign of life (global; no tenant data)."""

    def __init__(self, pool: ConnectionPool, *, name: str = WORKER_NAME) -> None:
        self._pool = pool
        self.name = name

    async def last_seen(self) -> datetime | None:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT last_seen_at FROM velora.worker_heartbeats WHERE name = %s", (self.name,)
            )
            row = await cur.fetchone()
        return None if row is None else row[0]

    async def beat(self, *, started_at: datetime, at: datetime) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO velora.worker_heartbeats (name, started_at, last_seen_at) "
                "VALUES (%s, %s, %s) ON CONFLICT (name) DO UPDATE "
                "SET started_at = EXCLUDED.started_at, last_seen_at = EXCLUDED.last_seen_at",
                (self.name, started_at, at),
            )


# Checked at startup: missing means the database wasn't migrated for this code.
REQUIRED_TABLES = ("velora.worker_heartbeats", "velora.recovery_runs")


async def missing_tables(pool: ConnectionPool) -> list[str]:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT t FROM unnest(%s::text[]) AS t WHERE to_regclass(t) IS NULL",
            (list(REQUIRED_TABLES),),
        )
        rows = await cur.fetchall()
    return [r[0] for r in rows]


@asynccontextmanager
async def hold_worker_lock(url: str) -> AsyncIterator[None]:
    """Hold the one-worker lock for the life of the block, on a connection of its own."""
    async with await AsyncConnection.connect(url, autocommit=True) as conn:
        cur = await conn.execute("SELECT pg_catalog.pg_try_advisory_lock(%s)", (WORKER_LOCK_KEY,))
        (locked,) = expect_row(await cur.fetchone())
        if not locked:
            raise WorkerAlreadyRunningError("another worker is running against this database")
        yield


# Customer messages received before recovery and not yet processed: the outage's backlog.
_CLOSE_BACKLOG = """
    UPDATE velora.recovery_runs r SET backlog_cleared_at = %(now)s
     WHERE r.backlog_cleared_at IS NULL
       AND NOT EXISTS (
           SELECT 1 FROM velora.inbox_events e
            WHERE e.event_type = 'message' AND e.status IN ('pending', 'processing')
              AND e.received_at < r.started_at)
    RETURNING r.id
"""


async def recover_tenant(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    *,
    outage_started_at: datetime | None,
    now: datetime,
    reconcile: InboundReconciler | None = None,
) -> TenantRecovery | None:
    """Steps 2 to 7 for one tenant, inside the caller's transaction. None if this tenant was
    already recovered from this outage."""
    cur = await conn.execute(
        "SELECT 1 FROM velora.recovery_runs "
        "WHERE outage_started_at IS NOT DISTINCT FROM %s::timestamptz",
        (outage_started_at,),
    )
    if await cur.fetchone() is not None:
        return None

    # 2. Leases held by the dead worker. The lock guarantees no live worker holds one.
    cur = await conn.execute(
        "UPDATE velora.inbox_events SET status = 'pending', available_at = %s, "
        "claim_token = NULL, locked_until = NULL WHERE status = 'processing'",
        (now,),
    )
    leases = cur.rowcount

    # 3. Inbound reconciliation (V-04).
    inbound: Literal["skipped", "reconciled"] = "skipped"
    recovered = 0
    if reconcile is not None and outage_started_at is not None:
        recovered = await reconcile(conn, tenant, outage_started_at, now)
        inbound = "reconciled"

    voided = extended = 0
    if outage_started_at is not None:
        # 4. Confirmations issued before the outage.
        voided = await void_pre_outage(conn, tenant, outage_started_at=outage_started_at, now=now)
        # 5. Transfer windows still open when the outage began. One that closed earlier
        #    was already late, and the order timers expire it as usual.
        cur = await conn.execute(
            "UPDATE velora.orders SET payment_due_at = payment_due_at + %(outage)s, "
            "updated_at = %(now)s "
            "WHERE status = 'awaiting_transfer' AND payment_due_at > %(start)s",
            {"outage": now - outage_started_at, "now": now, "start": outage_started_at},
        )
        extended = cur.rowcount

    # 6. Sends whose outcome the dead worker never recorded.
    cur = await conn.execute(
        "UPDATE velora.outbox_messages SET status = 'unknown', claim_token = NULL, "
        "locked_until = NULL, "
        "last_error = 'in flight when the worker stopped; outcome unknown' "
        "WHERE status = 'sending'"
    )
    unknown = cur.rowcount

    # 7. Stale replies, on every channel (held ones wait for theirs to reconnect). Customers
    #    just asked to confirm again (step 4) already have an apology.
    cur = await conn.execute(
        "SELECT ordering_key FROM velora.outbox_messages "
        "WHERE idempotency_key LIKE 'recovery-reconfirm:%%' AND created_at = %s",
        (now,),
    )
    told = {r[0] for r in await cur.fetchall()}
    stale = 0
    cur = await conn.execute("SELECT id FROM velora.channels WHERE state <> 'retired'")
    for (channel_id,) in await cur.fetchall():
        dropped, _ = await replace_stale(conn, tenant, channel_id, now, already_told=told)
        stale += dropped

    # 9. The run, and whether a backlog of held customer messages remains.
    run_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO velora.recovery_runs (id, tenant_id, outage_started_at, started_at, "
        "leases_reclaimed, inbound, inbound_recovered, nonces_voided, windows_extended, "
        "messages_unknown, replies_stale) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            run_id,
            tenant.value,
            outage_started_at,
            now,
            leases,
            inbound,
            recovered,
            voided,
            extended,
            unknown,
            stale,
        ),
    )
    cur = await conn.execute(_CLOSE_BACKLOG, {"now": now})
    backlog = await cur.fetchone() is None
    return TenantRecovery(
        tenant, leases, inbound, recovered, voided, extended, unknown, stale, backlog
    )


class Recovery:
    def __init__(
        self,
        db: Database,
        pool: ConnectionPool,
        dispatcher: Dispatcher,
        clock: Clock,
        *,
        heartbeat: Heartbeat | None = None,
        reconcile: InboundReconciler | None = None,
    ) -> None:
        self._db = db
        self._pool = pool
        self._dispatcher = dispatcher
        self._clock = clock
        self.heartbeat = heartbeat or Heartbeat(pool)
        self._reconcile = reconcile

    async def check_health(self) -> None:
        """Step 1. Raises RecoveryError if the database can't be used."""
        if not await self._db.ping():
            raise RecoveryError("the database does not answer")
        try:
            missing = await missing_tables(self._pool)
        except Exception as exc:
            raise RecoveryError("the database can't be read") from exc
        if missing:
            raise RecoveryError(
                f"the database is missing {', '.join(missing)}: run `python -m velora.db "
                "migrate up` first"
            )

    async def run(self) -> RecoveryReport:
        await self.check_health()
        now = self._clock.now()
        last_seen = await self.heartbeat.last_seen()
        outage_started_at = None if last_seen is None else min(last_seen + HEARTBEAT_INTERVAL, now)
        report = RecoveryReport(outage_started_at, now)
        for tenant in await self._dispatcher.tenants_for_recovery():
            try:
                async with self._db.tenant(tenant) as conn:
                    result = await recover_tenant(
                        conn,
                        tenant,
                        outage_started_at=outage_started_at,
                        now=now,
                        reconcile=self._reconcile,
                    )
            except Exception as exc:
                log.error("recovery failed for %s: %s", tenant.value, error_text(exc))
                raise RecoveryError(f"recovery failed for tenant {tenant.value}") from exc
            if result is None:
                report.already_done.append(tenant)
            else:
                report.tenants.append(result)
        # Only now: an interrupted recovery must find the same outage start next time.
        await self.heartbeat.beat(started_at=now, at=now)
        log.info(
            "recovery done: outage %s, %d tenant(s) recovered, %d already done",
            report.outage or "none (first start)",
            len(report.tenants),
            len(report.already_done),
        )
        return report

    def tracker(self, report: RecoveryReport) -> RecoveryTracker:
        open_runs = {t.tenant for t in report.tenants if t.backlog}
        # A run interrupted after this tenant was recovered may still have a backlog open.
        open_runs.update(report.already_done)
        return RecoveryTracker(self._db, self.heartbeat, self._clock, report.started_at, open_runs)


class RecoveryTracker:
    """Runs alongside the worker after recovery: the heartbeat, and closing each tenant's
    recovery window once its backlog of held customer messages has been processed."""

    def __init__(
        self,
        db: Database,
        heartbeat: Heartbeat,
        clock: Clock,
        started_at: datetime,
        open_runs: set[TenantId],
    ) -> None:
        self._db = db
        self._heartbeat = heartbeat
        self._clock = clock
        self._started_at = started_at
        self._open = set(open_runs)
        self._last_beat: datetime | None = None

    @property
    def open_runs(self) -> set[TenantId]:
        return set(self._open)

    async def beat(self, *, force: bool = False) -> None:
        now = self._clock.now()
        if force or self._last_beat is None or now - self._last_beat >= HEARTBEAT_INTERVAL:
            await self._heartbeat.beat(started_at=self._started_at, at=now)
            self._last_beat = now

    async def inbox_processed(self, tenant: TenantId) -> None:
        if tenant not in self._open:
            return
        async with self._db.tenant(tenant) as conn:
            cur = await conn.execute(_CLOSE_BACKLOG, {"now": self._clock.now()})
            closed = await cur.fetchone() is not None
            cur = await conn.execute(
                "SELECT 1 FROM velora.recovery_runs WHERE backlog_cleared_at IS NULL"
            )
            still_open = await cur.fetchone() is not None
        if closed or not still_open:
            self._open.discard(tenant)
