"""Request dependencies: services, the signed-in session, CSRF and permission checks (P3-T3).

- The session token lives in a `__Host-` cookie: HttpOnly, Secure, SameSite=Strict, Path=/.
- Any state-changing request (not GET/HEAD/OPTIONS) needs the `X-CSRF-Token` header, derived
  from the session (see `velora.identity.tokens.csrf_token_for`), and, when the browser sends
  an `Origin` header, it must be the dashboard's own origin.
- Any state-changing request also needs an `Idempotency-Key` header (AD-20, P9); see
  `velora.api.idempotency`.
- The tenant always comes from the authenticated session, never from the request.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from velora.api.idempotency import IdempotencyStore, claim_request
from velora.clock import Clock
from velora.config import Settings
from velora.conversation.service import ConversationService
from velora.db import Database
from velora.identity.permissions import Permission
from velora.identity.ratelimit import RateLimiter
from velora.identity.sessions import SessionService, StaffSession
from velora.identity.staff import StaffService
from velora.identity.tokens import csrf_matches
from velora.logcontext import bind_tenant
from velora.menu.service import MenuService
from velora.messaging.channel_admin import ChannelService
from velora.ordering.service import OrderingService
from velora.orders.service import OrderService
from velora.payments.service import PaymentService

SESSION_COOKIE = "__Host-velora_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Services:
    settings: Settings
    clock: Clock
    sessions: SessionService
    staff: StaffService
    menu: MenuService
    ordering: OrderingService
    orders: OrderService
    payments: PaymentService
    conversations: ConversationService
    login_limiter: RateLimiter
    idempotency: IdempotencyStore
    channels: ChannelService
    database: Database

    async def ping(self) -> bool:
        return await self.database.ping()


def get_services(request: Request) -> Services:
    services: Services | None = request.app.state.services
    if services is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database not configured")
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def check_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin != settings.public_origin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-origin request refused")


async def current_session(request: Request, services: ServicesDep) -> StaffSession:
    session = await services.sessions.authenticate(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    bind_tenant(session.tenant)
    if request.method not in SAFE_METHODS:
        check_origin(request, services.settings)
        if not csrf_matches(session.token, request.headers.get(CSRF_HEADER)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or wrong CSRF token")
        await claim_request(request, services.idempotency, session)
    return session


SessionDep = Annotated[StaffSession, Depends(current_session)]


def require(permission: Permission) -> Callable[[StaffSession], Awaitable[StaffSession]]:
    """Dependency: the signed-in session, if its role holds `permission`; otherwise 403."""

    async def dependency(session: SessionDep) -> StaffSession:
        if not session.can(permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"your role can't do this ({permission})"
            )
        return session

    return dependency
