"""Idempotency keys on dashboard actions (P9-T2, AD-20, AD-33).

Every state-changing request from a signed-in session must carry an `Idempotency-Key` header
(16 to 100 letters, digits, `-` or `_`; the dashboard sends a fresh random one per action).

- **Claim:** after the CSRF check, `current_session` claims the key for that session. The
  claim commits before the action runs.
- **Repeat:** a request with a key that already succeeded is not run again. It gets the first
  response's status, an empty `{"replayed": true}` body and `Idempotent-Replayed: true`, and
  the dashboard reloads what it shows. A double tap therefore makes one change.
- **In progress:** while the first request is still running, a repeat gets 409. A claim left
  behind by a crashed request expires after `ABANDONED_AFTER` and may be taken over.
- **Mismatch:** a key reused for a different method, path, query or body gets 422.
- **Failure:** an action that didn't succeed (any status outside 2xx, or an exception) frees
  its key, because its transaction rolled back and a retry should run.

Only the status is kept, never a response body, so no customer data or login link is stored
here. Rows older than `KEPT_FOR` are deleted when the session's restaurant claims a new key.

*Trade-off:* the claim and the action commit in separate transactions. If recording success
fails after the action committed, the key stays "in progress" until it is abandoned and a
retry can run the action again. Order moves (`from` guard), payments (status guard and
duplicate references) and outbox sends (their own idempotency key) refuse a second effect.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from velora.clock import Clock
from velora.db import Database
from velora.identity.errors import expect_row
from velora.identity.sessions import StaffSession

log = logging.getLogger(__name__)

HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,100}$")
ABANDONED_AFTER = timedelta(minutes=2)
KEPT_FOR = timedelta(hours=24)
_STATE_KEY = "velora_idempotency_claim"


@dataclass(frozen=True)
class Claim:
    session: StaffSession
    key: str


class ReplayedError(Exception):
    """This key already succeeded: answer with the first response's status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.status_code = status_code


def fingerprint(method: str, path: str, query: str, body: bytes) -> bytes:
    digest = hashlib.sha256()
    for part in (method.encode(), path.encode(), query.encode()):
        digest.update(len(part).to_bytes(4, "big") + part)
    digest.update(body)
    return digest.digest()


class IdempotencyStore:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def claim(self, session: StaffSession, key: str, print_: bytes) -> Claim:
        """Claim `key` for this session, or raise: ReplayedError, or 409 / 422."""
        now = self._clock.now()
        async with self._db.tenant(session.tenant) as conn:
            await conn.execute(
                "DELETE FROM velora.idempotency_keys WHERE created_at < %s", (now - KEPT_FOR,)
            )
            cur = await conn.execute(
                "INSERT INTO velora.idempotency_keys (tenant_id, session_id, key, fingerprint, "
                "state, created_at) VALUES (%s, %s, %s, %s, 'in_progress', %s) "
                "ON CONFLICT (tenant_id, session_id, key) DO NOTHING RETURNING id",
                (session.tenant.value, session.session_id, key, print_, now),
            )
            if await cur.fetchone() is not None:
                return Claim(session, key)
            cur = await conn.execute(
                "SELECT fingerprint, state, response_status, created_at "
                "FROM velora.idempotency_keys WHERE session_id = %s AND key = %s FOR UPDATE",
                (session.session_id, key),
            )
            stored, state, response_status, created_at = expect_row(await cur.fetchone())
            if bytes(stored) != print_:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "this Idempotency-Key was already used for a different request",
                )
            if state == "done":
                raise ReplayedError(response_status)
            if now - created_at < ABANDONED_AFTER:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "this action is already being processed"
                )
            # The first attempt never finished (the process stopped): take the key over.
            await conn.execute(
                "UPDATE velora.idempotency_keys SET created_at = %s "
                "WHERE session_id = %s AND key = %s",
                (now, session.session_id, key),
            )
            return Claim(session, key)

    async def finish(self, claim: Claim, status_code: int) -> None:
        """Record the outcome: kept on success, freed otherwise."""
        now = self._clock.now()
        async with self._db.tenant(claim.session.tenant) as conn:
            if 200 <= status_code < 300:
                await conn.execute(
                    "UPDATE velora.idempotency_keys SET state = 'done', response_status = %s, "
                    "completed_at = %s WHERE session_id = %s AND key = %s "
                    "AND state = 'in_progress'",
                    (status_code, now, claim.session.session_id, claim.key),
                )
            else:
                await conn.execute(
                    "DELETE FROM velora.idempotency_keys WHERE session_id = %s AND key = %s "
                    "AND state = 'in_progress'",
                    (claim.session.session_id, claim.key),
                )


async def claim_request(request: Request, store: IdempotencyStore, session: StaffSession) -> None:
    """Called by `current_session` for every state-changing request."""
    key = request.headers.get(HEADER)
    if key is None:
        raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED, f"{HEADER} header required")
    if not KEY_PATTERN.match(key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"malformed {HEADER} header")
    if _STATE_KEY in request.scope.setdefault("state", {}):
        return  # already claimed for this request (the dependency is shared)
    print_ = fingerprint(request.method, request.url.path, request.url.query, await request.body())
    claim = await store.claim(session, key, print_)
    request.scope["state"][_STATE_KEY] = (store, claim)


def replayed_response(exc: ReplayedError) -> Response:
    headers = {REPLAYED_HEADER: "true"}
    if exc.status_code == status.HTTP_204_NO_CONTENT:
        return Response(status_code=exc.status_code, headers=headers)
    return JSONResponse({"replayed": True}, status_code=exc.status_code, headers=headers)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Records each claimed key's outcome once the action has finished."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
        except Exception:
            await _finish(request, status.HTTP_500_INTERNAL_SERVER_ERROR)
            raise
        await _finish(request, response.status_code)
        return response


async def _finish(request: Request, status_code: int) -> None:
    claimed = request.scope.get("state", {}).pop(_STATE_KEY, None)
    if claimed is None:
        return
    store, claim = claimed
    try:
        await store.finish(claim, status_code)
    except Exception:
        # The action's own outcome stands. The key stays claimed until it is abandoned.
        log.exception("could not record the outcome of an idempotent request")
