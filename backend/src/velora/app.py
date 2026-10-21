"""FastAPI application factory (ingress service)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from velora import __version__
from velora.api.deps import Services
from velora.api.routes import router
from velora.clock import Clock, SystemClock
from velora.config import Settings
from velora.db import Database
from velora.identity.errors import (
    ConflictError,
    ForbiddenError,
    IdentityError,
    InvalidInputError,
    NotFoundError,
    PinRejectedError,
)
from velora.identity.ratelimit import RateLimiter
from velora.identity.sessions import SessionService
from velora.identity.staff import StaffService

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


def build_services(settings: Settings, database: Database, clock: Clock) -> Services:
    return Services(
        settings=settings,
        clock=clock,
        sessions=SessionService(database, clock),
        staff=StaffService(database, clock),
        login_limiter=RateLimiter(LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW, clock),
    )


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if database is not None or settings.database_url is None:
            yield
            return
        async with AsyncConnectionPool(settings.database_url, open=False) as pool:
            app.state.services = build_services(settings, Database(pool), clock)
            yield
        app.state.services = None

    app = FastAPI(title="VELORA", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.services = None if database is None else build_services(settings, database, clock)

    @app.exception_handler(IdentityError)
    async def identity_error(request: Request, exc: IdentityError) -> JSONResponse:
        code = _ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse({"detail": str(exc)}, status_code=code)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app
