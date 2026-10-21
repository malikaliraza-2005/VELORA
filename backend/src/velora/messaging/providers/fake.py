"""`FakeProvider`: simulated WhatsApp for tests and development (P5-T1, AD-25).

It keeps sent messages in memory, reports whatever session state a test sets, and fails the
next sends on request. Its webhooks are JSON signed with HMAC-SHA256 over the raw body, one
secret per session, so the ingress path is exercised the way a real adapter's would be.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import secrets
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from velora.messaging.provider import (
    OBSERVED_STATES,
    Capabilities,
    DeliveryStatus,
    InboundMedia,
    InboundMessage,
    InvalidWebhookError,
    MediaRef,
    ObservedState,
    ProviderEvent,
    SendError,
    SendResult,
    SessionState,
    WhatsAppProvider,
)

SIGNATURE_HEADER = "x-fake-signature"


@dataclass(frozen=True)
class SentMessage:
    session_ref: str
    to: str
    text: str | None
    media: MediaRef | None
    idempotency_key: str
    provider_message_id: str


class FakeProvider(WhatsAppProvider):
    name = "fake"

    def __init__(self, *, idempotent_send: bool = True) -> None:
        self._idempotent = idempotent_send
        self.sent: list[SentMessage] = []
        self._states: dict[str, ObservedState] = {}
        self._failures: dict[str, deque[SendError]] = defaultdict(deque)
        self._by_key: dict[tuple[str, str], SendResult] = {}
        self._secrets: dict[str, bytes] = {}
        self._ids = itertools.count(1)
        self.status_error: Exception | None = None

    # Test controls ------------------------------------------------------------------------

    def set_state(self, session_ref: str, state: ObservedState) -> None:
        self._states[session_ref] = state

    def fail_next(self, session_ref: str, *errors: SendError) -> None:
        """The next sends on this session raise these errors, in order."""
        self._failures[session_ref].extend(errors)

    def webhook_secret(self, session_ref: str) -> bytes:
        return self._secrets.setdefault(session_ref, secrets.token_bytes(32))

    def signed_webhook(
        self, session_ref: str, events: list[ProviderEvent], *, payload_session: str | None = None
    ) -> tuple[dict[str, str], bytes]:
        """A webhook as this provider would send it: headers and raw body."""
        body = json.dumps(
            {
                "session": payload_session or session_ref,
                "events": [_encode(e) for e in events],
            }
        ).encode()
        digest = hmac.new(self.webhook_secret(session_ref), body, hashlib.sha256).hexdigest()
        return {SIGNATURE_HEADER: f"sha256={digest}"}, body

    def texts_to(self, to: str) -> list[str]:
        return [m.text for m in self.sent if m.to == to and m.text is not None]

    # WhatsAppProvider ---------------------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return Capabilities(
            buttons=False,
            lists=False,
            media=True,
            idempotent_send=self._idempotent,
            max_text_length=4096,
        )

    async def send_text(
        self, session_ref: str, to: str, text: str, *, idempotency_key: str
    ) -> SendResult:
        return self._send(session_ref, to, text, None, idempotency_key)

    async def send_media(
        self,
        session_ref: str,
        to: str,
        media: MediaRef,
        *,
        caption: str | None,
        idempotency_key: str,
    ) -> SendResult:
        return self._send(session_ref, to, caption, media, idempotency_key)

    def _send(
        self, session_ref: str, to: str, text: str | None, media: MediaRef | None, key: str
    ) -> SendResult:
        failures = self._failures[session_ref]
        if failures:
            raise failures.popleft()
        if self._idempotent and (session_ref, key) in self._by_key:
            return self._by_key[(session_ref, key)]
        result = SendResult(f"fake-{next(self._ids)}")
        self._by_key[(session_ref, key)] = result
        self.sent.append(SentMessage(session_ref, to, text, media, key, result.provider_message_id))
        return result

    async def get_session_status(self, session_ref: str) -> ObservedState:
        if self.status_error is not None:
            raise self.status_error
        return self._states.get(session_ref, "connected")

    async def start_session(self, session_ref: str) -> None:
        self._states.setdefault(session_ref, "needs_qr")

    async def get_qr(self, session_ref: str) -> str | None:
        if self._states.get(session_ref) != "needs_qr":
            return None
        return f"fake-qr:{session_ref}"

    async def logout(self, session_ref: str) -> None:
        self._states[session_ref] = "logged_out"

    def parse_webhook(
        self, session_ref: str, headers: Mapping[str, str], body: bytes
    ) -> list[ProviderEvent]:
        lowered = {k.lower(): v for k, v in headers.items()}
        expected = hmac.new(self.webhook_secret(session_ref), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(lowered.get(SIGNATURE_HEADER, ""), f"sha256={expected}"):
            raise InvalidWebhookError("bad webhook signature")
        try:
            data = json.loads(body)
            if data["session"] != session_ref:
                raise InvalidWebhookError("webhook names another session")
            return [_decode(session_ref, e) for e in data["events"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidWebhookError("malformed webhook") from exc


def _encode(event: ProviderEvent) -> dict[str, Any]:
    kind = {InboundMessage: "message", DeliveryStatus: "status", SessionState: "session"}
    fields = asdict(event)
    fields.pop("session_ref")
    for key, value in fields.items():
        if isinstance(value, datetime):
            fields[key] = value.isoformat()
    return {"type": kind[type(event)], **fields}


def _decode(session_ref: str, raw: dict[str, Any]) -> ProviderEvent:
    fields = dict(raw)
    kind = fields.pop("type")
    if kind == "message":
        media = fields.pop("media")
        return InboundMessage(
            session_ref=session_ref,
            received_at=datetime.fromisoformat(fields.pop("received_at")),
            media=None if media is None else InboundMedia(**media),
            **fields,
        )
    if kind == "status":
        return DeliveryStatus(
            session_ref=session_ref,
            occurred_at=datetime.fromisoformat(fields.pop("occurred_at")),
            **fields,
        )
    if kind == "session":
        if fields["state"] not in OBSERVED_STATES:
            raise ValueError("unknown session state")
        return SessionState(
            session_ref=session_ref,
            occurred_at=datetime.fromisoformat(fields.pop("occurred_at")),
            **fields,
        )
    raise ValueError(f"unknown event type {kind!r}")
