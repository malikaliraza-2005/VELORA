"""The worker loop (P5, P7): health poll, inbox, order timers and outbox, tenant by tenant.

Each round asks the dispatcher which tenants have work, then works on each inside its own
tenant scope. One tenant's failure is logged (redacted) and doesn't stop the others. Each
round has its own correlation id, and log lines name the tenant being worked on (P10-T1).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from velora.clock import Clock
from velora.logcontext import log_context, new_correlation_id
from velora.messaging.channels import HEALTH_POLL_INTERVAL, ChannelMonitor
from velora.messaging.dispatch import Dispatcher
from velora.messaging.inbox import InboxProcessor
from velora.messaging.outbox import OutboxSender
from velora.orders.timers import OrderTimers
from velora.redaction import error_text

log = logging.getLogger(__name__)

ROUND_INTERVAL = timedelta(seconds=1)


class Worker:
    def __init__(
        self,
        dispatcher: Dispatcher,
        inbox: InboxProcessor,
        outbox: OutboxSender,
        monitor: ChannelMonitor,
        clock: Clock,
        *,
        health_interval: timedelta = HEALTH_POLL_INTERVAL,
        timers: OrderTimers | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._inbox = inbox
        self._outbox = outbox
        self._monitor = monitor
        self._timers = timers
        self._clock = clock
        self._health_interval = health_interval
        self._last_poll: datetime | None = None

    async def run_round(self) -> None:
        with log_context(correlation_id=new_correlation_id()):
            await self._round()

    async def _round(self) -> None:
        now = self._clock.now()
        if self._last_poll is None or now - self._last_poll >= self._health_interval:
            self._last_poll = now
            for tenant in await self._dispatcher.tenants_with_channels():
                try:
                    with log_context(tenant=tenant):
                        await self._monitor.poll(tenant)
                except Exception as exc:
                    log.error("health poll failed for %s: %s", tenant.value, error_text(exc))
        for tenant in await self._dispatcher.tenants_with_work(self._inbox.event_types):
            try:
                with log_context(tenant=tenant):
                    await self._inbox.run_once(tenant)
                    if self._timers is not None:
                        # Before the outbox, so an expiry notice goes out in the same round.
                        await self._timers.run_once(tenant)
                    await self._outbox.run_once(tenant)
            except Exception as exc:
                log.error("queue work failed for %s: %s", tenant.value, error_text(exc))

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_round()
            except Exception as exc:
                log.error("worker round failed: %s", error_text(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), ROUND_INTERVAL.total_seconds())
