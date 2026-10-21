"""Tenant-scoped bearer tokens for magic links and device sessions (P3-T2, P3-T3).

Wire format: `<tenant uuid, 32 hex>.<secret, 43 base64url chars>`. The secret is 32 random
bytes. Only its SHA-256 is stored (`magic_links.token_hash`, `staff_sessions.token_hash`).

The tenant part is an unverified lookup hint, never an authority. It only chooses which
tenant's RLS scope to search; a token is accepted only if the hash of its secret matches a row
inside that scope. Pairing tenant B's id with a secret issued for tenant A finds nothing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass

from velora.db import TenantId

_SECRET_BYTES = 32
_WIRE = re.compile(r"^(?P<tenant>[0-9a-f]{32})\.(?P<secret>[A-Za-z0-9_-]{43})$")


@dataclass(frozen=True, slots=True)
class ScopedToken:
    tenant: TenantId
    secret: bytes

    @property
    def hash(self) -> bytes:
        return hashlib.sha256(self.secret).digest()

    def wire(self) -> str:
        encoded = base64.urlsafe_b64encode(self.secret).rstrip(b"=").decode("ascii")
        return f"{self.tenant.value.hex}.{encoded}"


def new_token(tenant: TenantId) -> ScopedToken:
    return ScopedToken(tenant, secrets.token_bytes(_SECRET_BYTES))


def parse_token(raw: str | None) -> ScopedToken | None:
    """Parse a wire token, or return None if it is malformed in any way."""
    if raw is None:
        return None
    match = _WIRE.fullmatch(raw)
    if match is None:
        return None
    secret = base64.urlsafe_b64decode(match["secret"] + "=")
    if len(secret) != _SECRET_BYTES:
        return None
    return ScopedToken(TenantId(uuid.UUID(hex=match["tenant"])), secret)


def csrf_token_for(session: ScopedToken) -> str:
    """The CSRF token for a session, derived from its secret, so nothing extra is stored."""
    mac = hmac.new(session.secret, b"velora-csrf-v1", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def csrf_matches(session: ScopedToken, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(csrf_token_for(session), presented)
