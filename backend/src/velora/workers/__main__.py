"""`python -m velora.workers`: run the messaging worker (P5).

Needs VELORA_DATABASE_URL (app_rw) and VELORA_DISPATCHER_DATABASE_URL (velora_dispatcher).
This is a composition root: it may import provider adapters through the registry.

Inbound customer messages stay stored in the inbox until P6 registers their handler; only
delivery receipts are processed for now.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys

from psycopg_pool import AsyncConnectionPool

from velora.clock import SystemClock
from velora.config import Settings
from velora.db import Database
from velora.messaging.channels import ChannelMonitor
from velora.messaging.dispatch import Dispatcher
from velora.messaging.inbox import InboxProcessor
from velora.messaging.outbox import OutboxSender, apply_delivery_status
from velora.messaging.registry import build_providers
from velora.redaction import configure_logging
from velora.workers.runner import Worker


async def _run(settings: Settings, app_url: str, dispatcher_url: str) -> None:
    clock = SystemClock()
    providers = build_providers(settings)
    async with (
        AsyncConnectionPool(app_url, open=False) as app_pool,
        AsyncConnectionPool(dispatcher_url, open=False, min_size=1, max_size=2) as d_pool,
    ):
        db = Database(app_pool)
        worker = Worker(
            Dispatcher(d_pool, clock),
            InboxProcessor(db, clock, {"delivery_status": apply_delivery_status}),
            OutboxSender(db, providers, clock),
            ChannelMonitor(db, providers, clock),
            clock,
        )
        stop = asyncio.Event()
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
        await worker.run_forever(stop)


def main() -> int:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    app_url, dispatcher_url = settings.database_url, settings.dispatcher_database_url
    if app_url is None or dispatcher_url is None:
        print("VELORA_DATABASE_URL and VELORA_DISPATCHER_DATABASE_URL must be set", file=sys.stderr)
        return 2
    # psycopg's async mode cannot use the Windows proactor event loop.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(settings, app_url, dispatcher_url), loop_factory=loop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
