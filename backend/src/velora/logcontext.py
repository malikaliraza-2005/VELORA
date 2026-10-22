"""Log context (P10-T1): the tenant and correlation id that every log line carries.

The API binds a correlation id per request (`X-Request-ID`, see `velora.api.web`) and the
tenant once the session is known. The worker binds a fresh correlation id per round and the
tenant while it works on one. Values live in context variables, so concurrent requests and
tasks never see each other's.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from velora.db import TenantId

_tenant: ContextVar[str | None] = ContextVar("velora_tenant", default=None)
_correlation: ContextVar[str | None] = ContextVar("velora_correlation", default=None)

# What a caller may supply as X-Request-ID; anything else is replaced by our own id.
CORRELATION_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_correlation_id() -> str:
    return secrets.token_hex(8)


def accept_correlation_id(value: str | None) -> str:
    """The caller's id when it is well formed, otherwise a new one."""
    return value if value is not None and CORRELATION_ID.match(value) else new_correlation_id()


def current_tenant() -> str | None:
    return _tenant.get()


def current_correlation_id() -> str | None:
    return _correlation.get()


def bind_tenant(tenant: TenantId | None) -> None:
    """Set the tenant for the rest of the current context (e.g. one request)."""
    _tenant.set(None if tenant is None else str(tenant.value))


@contextmanager
def log_context(
    *, tenant: TenantId | None = None, correlation_id: str | None = None
) -> Iterator[None]:
    """Bind the tenant and/or correlation id inside the block, then restore the old ones."""
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    if tenant is not None:
        tokens.append((_tenant, _tenant.set(str(tenant.value))))
    if correlation_id is not None:
        tokens.append((_correlation, _correlation.set(correlation_id)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
