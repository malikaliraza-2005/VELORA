"""FastAPI application factory (ingress service and the staff dashboard).

WhatsApp provider adapters are passed in (`providers`) by a composition root
(`velora.asgi`), so this module never imports one (P5-T8). Without them, the owner's QR
re-link answers that it can't re-link from here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, Response
from psycopg_pool import AsyncConnectionPool

from velora import __version__
from velora.api import channels, conversations, menu, ordering, orders, payments, summary
from velora.api.correlation import CorrelationMiddleware
from velora.api.deps import Services
from velora.api.idempotency import (
    IdempotencyMiddleware,
    IdempotencyStore,
    ReplayedError,
    replayed_response,
)
from velora.api.routes import router
from velora.api.web import SecurityHeadersMiddleware, default_web_dir, mount_dashboard
from velora.clock import Clock, SystemClock
from velora.config import Settings
from velora.conversation.service import ConversationService
from velora.db import Database
from velora.identity.errors import (
    ConflictError,
    ForbiddenError,
    IdentityError,
    InvalidInputError,
    NotFoundError,
    PinRejectedError,
    ValidationProblemsError,
)
from velora.identity.ratelimit import RateLimiter
from velora.identity.sessions import SessionService
from velora.identity.staff import StaffService
from velora.menu.service import MenuService
from velora.messaging.channel_admin import ChannelService
from velora.messaging.provider import WhatsAppProvider
from velora.ordering.service import OrderingService
from velora.orders.service import OrderService
from velora.payments.service import PaymentService

# Per client address: 10 login attempts per 5 minutes.
LOGIN_RATE_LIMIT = 10
LOGIN_RATE_WINDOW = timedelta(minutes=5)

_ERROR_STATUS: dict[type[IdentityError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ForbiddenError: status.HTTP_403_FORBIDDEN,
    PinRejectedError: status.HTTP_403_FORBIDDEN,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def build_services(
    settings: Settings,
    database: Database,
    clock: Clock,
    providers: Mapping[str, WhatsAppProvider],
) -> Services:
    staff = StaffService(database, clock)
    return Services(
        settings=settings,
        clock=clock,
        sessions=SessionService(database, clock),
        staff=staff,
        menu=MenuService(database, clock),
        ordering=OrderingService(database, clock),
        orders=OrderService(database, clock),
        payments=PaymentService(database, clock, staff),
        conversations=ConversationService(database, clock),
        login_limiter=RateLimiter(LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW, clock),
        idempotency=IdempotencyStore(database, clock),
        channels=ChannelService(database, providers, clock),
        database=database,
    )


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    clock: Clock | None = None,
    providers: Mapping[str, WhatsAppProvider] | None = None,
    web_dir: Path | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    providers = providers or {}
    web_dir = web_dir or default_web_dir()
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if database is not None or settings.database_url is None:
            yield
            return
        async with AsyncConnectionPool(settings.database_url, open=False) as pool:
            app.state.services = build_services(settings, Database(pool), clock, providers)
            yield
        app.state.services = None

    app = FastAPI(title="VELORA", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.services = (
        None if database is None else build_services(settings, database, clock, providers)
    )

    @app.exception_handler(IdentityError)
    async def identity_error(request: Request, exc: IdentityError) -> JSONResponse:
        code = next(
            (code for kind, code in _ERROR_STATUS.items() if isinstance(exc, kind)),
            status.HTTP_400_BAD_REQUEST,
        )
        if isinstance(exc, ValidationProblemsError):
            return JSONResponse(
                {"detail": str(exc), "problems": exc.problems},
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return JSONResponse({"detail": str(exc)}, status_code=code)

    @app.exception_handler(ReplayedError)
    async def replayed(request: Request, exc: ReplayedError) -> Response:
        return replayed_response(exc)

    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    # Outermost, so every log line of a request carries its correlation id.
    app.add_middleware(CorrelationMiddleware)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness: the process answers."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        """Readiness, for the monitor's probe and the external uptime check (P10-T3): the
        database answers. Says nothing else about the deployment."""
        services: Services | None = request.app.state.services
        if services is None or not await services.ping():
            return JSONResponse(
                {"status": "unavailable"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return JSONResponse({"status": "ok"})

    app.include_router(router)
    app.include_router(menu.router)
    app.include_router(ordering.router)
    app.include_router(conversations.router)
    app.include_router(orders.router)
    app.include_router(payments.router)
    app.include_router(channels.router)
    app.include_router(summary.router)
    if web_dir is not None:
        mount_dashboard(app, web_dir)
    return app
