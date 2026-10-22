"""`python -m velora.workers`: run the messaging worker (P5).

Needs VELORA_DATABASE_URL (app_rw) and VELORA_DISPATCHER_DATABASE_URL (velora_dispatcher).
This is a composition root: it may import provider adapters through the registry.

Inbound customer messages go to the conversation engine (P6), delivery receipts to the
outbox. Order timers (P7) expire unpaid transfer orders. The AI proposer is optional
(`VELORA_GROQ_API_KEY`); without it customers use the numbered menu.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys

from psycopg_pool import AsyncConnectionPool

from velora.ai.registry import build_proposer
from velora.clock import SystemClock
from velora.config import Settings
from velora.conversation.engine import ConversationEngine
from velora.db import Database
from velora.messaging.channels import ChannelMonitor
from velora.messaging.dispatch import Dispatcher
from velora.messaging.inbox import InboxProcessor
from velora.messaging.outbox import OutboxSender, apply_delivery_status
from velora.messaging.registry import build_providers
from velora.orders.timers import OrderTimers
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
        engine = ConversationEngine(
            clock, providers, build_proposer(settings), ai_timeout=settings.ai_timeout_seconds
        )
        handlers = {"message": engine.handle, "delivery_status": apply_delivery_status}
        worker = Worker(
            Dispatcher(d_pool, clock),
            InboxProcessor(db, clock, handlers),
            OutboxSender(db, providers, clock),
            ChannelMonitor(db, providers, clock),
            clock,
            timers=OrderTimers(db, clock),
        )
        stop = asyncio.Event()
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
        await worker.run_forever(stop)


def main() -> int:
    settings = Settings.from_env()
    configure_logging(settings.log_level, fmt=settings.log_format)
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
