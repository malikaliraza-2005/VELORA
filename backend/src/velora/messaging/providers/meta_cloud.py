"""Meta WhatsApp Cloud API adapter: interface only (P5-T7, AD-06).

It exists so the interface is proven to fit a second provider. It is **not implemented**, and
every method raises. Implementing it needs its own decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NoReturn

from velora.messaging.provider import (
    Capabilities,
    MediaRef,
    ObservedState,
    ProviderEvent,
    SendResult,
    WhatsAppProvider,
)


def _not_implemented() -> NoReturn:
    raise NotImplementedError("the Meta Cloud API adapter is interface-only (AD-06)")


class MetaCloudAdapter(WhatsAppProvider):
    name = "meta_cloud"

    def capabilities(self) -> Capabilities:
        _not_implemented()

    async def send_text(
        self, session_ref: str, to: str, text: str, *, idempotency_key: str
    ) -> SendResult:
        _not_implemented()

    async def send_media(
        self,
        session_ref: str,
        to: str,
        media: MediaRef,
        *,
        caption: str | None,
        idempotency_key: str,
    ) -> SendResult:
        _not_implemented()

    async def get_session_status(self, session_ref: str) -> ObservedState:
        _not_implemented()

    async def start_session(self, session_ref: str) -> None:
        _not_implemented()

    async def get_qr(self, session_ref: str) -> str | None:
        _not_implemented()

    async def logout(self, session_ref: str) -> None:
        _not_implemented()

    def parse_webhook(
        self, session_ref: str, headers: Mapping[str, str], body: bytes
    ) -> list[ProviderEvent]:
        _not_implemented()
