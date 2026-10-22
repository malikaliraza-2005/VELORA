"""P10 exit criterion: each alert fires in a drill, end to end through the monitor loop.

1. Kill the ingress: the API's readiness probe stops answering.
2. Disconnect a session: the phone is unlinked; the worker's health poll records `needs_qr`.
3. Fill the dead-letter queue: inbox events that keep failing until they are `failed`.
4. Fail a backup: the backup job reports a failed run.

Each drill also checks that the alert is recorded in `alert_events`, is not repeated on the
next cycle, and is resolved once the problem is gone. The notifier is a recording stand-in:
real delivery to the founder's phone or email needs a vendor (PC-11) and is checked with
`python -m velora.monitoring test-alert` in staging.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from tests.db.conftest import PgDatabase
from tests.messaging.conftest import ScopedDispatcher, new_shop
from tests.monitoring.conftest import MonEnv, monitor_conn
from tests.monitoring.test_metrics import failing, receive
from velora.messaging.channels import ChannelMonitor
from velora.messaging.inbox import InboxProcessor
from velora.messaging.outbox import OutboxSender
from velora.monitoring.alerts import AlertManager, AlertStore, RuleSettings
from velora.monitoring.metrics import record_backup
from velora.monitoring.notify import NotifyError, ReadinessProbe
from velora.monitoring.runner import Monitor
from velora.workers.runner import Worker

RULES = RuleSettings(expect_backups=False, backup_max_age=timedelta(hours=2))


def monitor_for(mon: MonEnv, *, probe: ReadinessProbe | None = None) -> Monitor:
    return Monitor(
        mon.connect,
        mon.env.clock,
        AlertManager(AlertStore(mon.connect), [mon.notifier]),
        RULES,
        notifiers=[mon.notifier],
        probe=probe,
        daily_report=False,
    )


async def recorded(mon: MonEnv, prefix: str) -> list[tuple[str, bool]]:
    async with monitor_conn(mon) as conn:
        cur = await conn.execute(
            "SELECT state, delivered FROM velora.alert_events WHERE alert_key LIKE %s ORDER BY seq",
            (prefix + "%",),
        )
        return list(await cur.fetchall())


def fired(mon: MonEnv, prefix: str, state: str = "firing") -> int:
    return sum(1 for n in mon.notifier.sent if n.key.startswith(prefix) and n.state == state)


def test_drill_kill_the_ingress(run: Any) -> None:
    api = {"up": True}

    async def transport(method: str, url: str, body: bytes | None, timeout: float) -> int:
        if not api["up"]:
            raise NotifyError("request failed: ConnectionRefusedError")
        return 200

    async def scenario(mon: MonEnv) -> list[Any]:
        monitor = monitor_for(mon, probe=ReadinessProbe("http://api:8000/readyz", transport))
        await monitor.cycle()
        api["up"] = False  # the drill: stop the API container
        await monitor.cycle()
        first = fired(mon, "api_down")
        await monitor.cycle()
        second = fired(mon, "api_down")
        await monitor.cycle()
        api["up"] = True
        await monitor.cycle()
        return [
            first,
            second,
            fired(mon, "api_down"),
            fired(mon, "api_down", "resolved"),
            await recorded(mon, "api_down"),
        ]

    first, second, total, resolved, rows = run(scenario)
    assert (first, second, total, resolved) == (0, 1, 1, 1)
    assert rows == [("firing", True), ("resolved", True)]


def test_drill_disconnect_a_session(run: Any, mondb: PgDatabase) -> None:
    shop = new_shop(mondb)

    async def scenario(mon: MonEnv) -> list[Any]:
        env = mon.env
        worker = Worker(
            ScopedDispatcher(env.dispatcher, shop),
            InboxProcessor(env.db, env.clock, {}),
            OutboxSender(env.db, env.providers, env.clock),
            ChannelMonitor(env.db, env.providers, env.clock),
            env.clock,
        )
        monitor = monitor_for(mon)
        key = f"channel_down:{shop.channel_id}"
        env.fake.set_state(shop.session_ref, "needs_qr")  # the drill: unlink the phone
        await worker.run_round()
        await monitor.cycle()
        early = fired(mon, key)
        env.clock.advance(timedelta(minutes=5))
        await monitor.cycle()
        sent = [n for n in mon.notifier.sent if n.key == key]
        env.fake.set_state(shop.session_ref, "connected")  # scanned the QR again
        env.clock.advance(timedelta(minutes=1))
        await worker.run_round()
        await monitor.cycle()
        return [early, sent, fired(mon, key, "resolved"), await recorded(mon, key)]

    early, sent, resolved, rows = run(scenario)
    assert early == 0
    (alert,) = sent
    assert alert.severity == "critical"
    assert "needs_qr for 5 min" in alert.title
    assert resolved == 1
    assert [state for state, _ in rows] == ["firing", "resolved"]


def test_drill_fill_the_dead_letter_queue(run: Any, mondb: PgDatabase) -> None:
    shop = new_shop(mondb)

    async def scenario(mon: MonEnv) -> list[Any]:
        env = mon.env
        processor = InboxProcessor(env.db, env.clock, {"message": failing})
        monitor = monitor_for(mon)

        async def fail_all() -> None:
            for _ in range(7):
                await processor.run_once(shop.tenant)
                env.clock.advance(timedelta(minutes=20))

        await monitor.cycle()
        before = fired(mon, "dead_letters")
        await receive(mon, shop, "dlq-1")
        await fail_all()
        await monitor.cycle()
        once = [n.title for n in mon.notifier.sent if n.key == "dead_letters"]
        await monitor.cycle()
        await receive(mon, shop, "dlq-2")  # the queue grows: sent again
        await fail_all()
        await monitor.cycle()
        return [before, once, [n.title for n in mon.notifier.sent if n.key == "dead_letters"]]

    before, once, later = run(scenario)
    assert before == 0
    assert len(once) == 1 and "1 inbox event(s) failed" in once[0]
    assert len(later) == 2 and "2 inbox event(s) failed" in later[1]


def test_drill_fail_a_backup(run: Any) -> None:
    async def scenario(mon: MonEnv) -> list[Any]:
        monitor = monitor_for(mon)

        async def report(ok: bool) -> None:
            async with monitor_conn(mon) as conn:
                await record_backup(
                    conn,
                    ok=ok,
                    kind="hourly-dump",
                    detail=None if ok else "disk full",
                    at=mon.env.clock.now(),
                )
            mon.env.clock.advance(timedelta(minutes=1))

        await report(False)  # the drill: the backup job fails
        await monitor.cycle()
        sent = [n for n in mon.notifier.sent if n.key == "backup_failed"]
        await report(True)
        await monitor.cycle()
        return [sent, fired(mon, "backup_failed", "resolved")]

    sent, resolved = run(scenario)
    (alert,) = sent
    assert alert.severity == "critical" and "disk full" in alert.title
    assert resolved == 1


def test_a_restarted_monitor_does_not_repeat_open_alerts(run: Any) -> None:
    async def scenario(mon: MonEnv) -> int:
        async with monitor_conn(mon) as conn:
            await record_backup(
                conn, ok=False, kind="restart-drill", detail=None, at=mon.env.clock.now()
            )
        await monitor_for(mon).cycle()
        await monitor_for(mon).cycle()  # a new process reads what was already sent
        async with monitor_conn(mon) as conn:
            await record_backup(
                conn, ok=True, kind="restart-drill", detail=None, at=mon.env.clock.now()
            )
        await monitor_for(mon).cycle()
        return fired(mon, "backup_failed")

    assert run(scenario) == 1


def test_the_monitor_alerts_when_it_cannot_read_the_database(run: Any) -> None:
    async def scenario(mon: MonEnv) -> list[str]:
        async def broken() -> Any:
            raise ConnectionError("connection refused password=abcdefgh123")

        monitor = Monitor(
            broken,
            mon.env.clock,
            AlertManager(AlertStore(broken), [mon.notifier]),
            RULES,
            notifiers=[mon.notifier],
        )
        await monitor.cycle()
        await monitor.cycle()
        return mon.notifier.keys()

    assert run(scenario) == ["monitor_database"]
