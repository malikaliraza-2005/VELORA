"""Correlation ids for requests (P10-T1).

Every request gets one: the caller's `X-Request-ID` when it is well formed, otherwise a new
one. It is bound to the log context for the whole request and returned in `X-Request-ID`, so
a staff member's report ("it said error abc123…") leads to the matching log lines. A server
error is logged once here with the method, route and status, never the query or body.
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from velora.logcontext import accept_correlation_id, log_context

log = logging.getLogger(__name__)

HEADER = "X-Request-ID"


class CorrelationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                supplied = value.decode("latin-1")
                break
        correlation_id = accept_correlation_id(supplied)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER.lower().encode(), correlation_id.encode()))
                message = {**message, "headers": headers}
                if message["status"] >= 500:
                    log.error(
                        "%s %s answered %s", scope["method"], scope["path"], message["status"]
                    )
            await send(message)

        with log_context(correlation_id=correlation_id):
            try:
                await self.app(scope, receive, send_with_id)
            except Exception:
                log.exception("%s %s failed", scope["method"], scope["path"])
                raise
