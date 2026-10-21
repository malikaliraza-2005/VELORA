"""The `WhatsAppProvider` interface and the canonical events (P5-T1, AD-05, AD-25).

Business code depends on this module only. Adapters live in `velora.messaging.providers`, and
only the composition roots may import them (a static check enforces it, P5-T8).

An adapter:
- verifies each webhook on the raw body and maps it to canonical events (`parse_webhook`);
- normalises senders to E.164 phone numbers and computes each event's `dedup_key` (V-05);
- raises the `SendError` subclass that says what is known about a failed send (V-06).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# What a provider can report about a session.
ObservedState = Literal[
    "connected", "reconnecting", "needs_qr", "logged_out", "failed", "suspected_restricted"
]
# Plus the states only VELORA sets: not yet linked, and taken out of service.
ChannelState = ObservedState | Literal["pending", "retired"]
OBSERVED_STATES: frozenset[str] = frozenset(
    {"connected", "reconnecting", "needs_qr", "logged_out", "failed", "suspected_restricted"}
)

# Group, status and broadcast events are dropped at ingress and never stored (AD-24).
ChatKind = Literal["direct", "group", "status", "broadcast"]
MessageKind = Literal["text", "image", "audio", "video", "document", "location", "sticker", "other"]
DeliveryState = Literal["sent", "delivered", "read", "failed"]


@dataclass(frozen=True)
class Capabilities:
    buttons: bool
    lists: bool
    media: bool
    # True if re-sending with the same idempotency key can never deliver twice. Without it,
    # a send whose outcome is unknown is never retried automatically.
    idempotent_send: bool
    max_text_length: int


@dataclass(frozen=True)
class InboundMedia:
    provider_media_id: str
    mime_type: str


@dataclass(frozen=True)
class InboundMessage:
    session_ref: str
    # Unique per message on this session; repeated deliveries carry the same key.
    dedup_key: str
    provider_message_id: str
    # E.164, e.g. +923001234567.
    sender: str
    chat_kind: ChatKind
    kind: MessageKind
    text: str | None
    received_at: datetime
    media: InboundMedia | None = None


@dataclass(frozen=True)
class DeliveryStatus:
    session_ref: str
    dedup_key: str
    provider_message_id: str
    status: DeliveryState
    occurred_at: datetime
    error: str | None = None


@dataclass(frozen=True)
class SessionState:
    session_ref: str
    state: ObservedState
    occurred_at: datetime
    detail: str | None = None


ProviderEvent = InboundMessage | DeliveryStatus | SessionState


@dataclass(frozen=True)
class MediaRef:
    url: str
    mime_type: str
    filename: str | None = None


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str


class SendError(Exception):
    """A send did not succeed. The subclass says what is known about the outcome."""


class ChannelDownError(SendError):
    """The session can't send at all. The message was not sent; it waits for the channel."""

    def __init__(self, state: ObservedState, message: str = "channel is not connected") -> None:
        super().__init__(message)
        self.state = state


class TransientSendError(SendError):
    """The message was not sent, and trying again later may work."""


class PermanentSendError(SendError):
    """The message was not sent, and trying again will not help (e.g. an invalid recipient)."""


class SendTimeoutError(SendError):
    """No answer in time: the message may or may not have been sent (V-06)."""


class InvalidWebhookError(Exception):
    """A webhook failed verification or could not be parsed. Nothing from it is stored."""


class WhatsAppProvider(ABC):
    """One WhatsApp integration. `name` matches `channels.provider`."""

    name: str

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    async def send_text(
        self, session_ref: str, to: str, text: str, *, idempotency_key: str
    ) -> SendResult: ...

    @abstractmethod
    async def send_media(
        self,
        session_ref: str,
        to: str,
        media: MediaRef,
        *,
        caption: str | None,
        idempotency_key: str,
    ) -> SendResult: ...

    @abstractmethod
    async def get_session_status(self, session_ref: str) -> ObservedState: ...

    @abstractmethod
    async def start_session(self, session_ref: str) -> None: ...

    @abstractmethod
    async def get_qr(self, session_ref: str) -> str | None:
        """The pairing QR code while the session needs one, otherwise None."""

    @abstractmethod
    async def logout(self, session_ref: str) -> None: ...

    @abstractmethod
    def parse_webhook(
        self, session_ref: str, headers: Mapping[str, str], body: bytes
    ) -> list[ProviderEvent]:
        """Verify the webhook on its raw body, then map it to canonical events.

        `session_ref` is the session the webhook URL belongs to. Raises InvalidWebhookError
        on a bad signature or payload, or if the payload names another session.
        """
