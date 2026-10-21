"""Composition root: which provider adapters this deployment runs (P5-T8).

Only this module and `velora.workers.__main__` may import `velora.messaging.providers`.
"""

from __future__ import annotations

from velora.config import Settings
from velora.messaging.provider import WhatsAppProvider
from velora.messaging.providers.fake import FakeProvider


def build_providers(settings: Settings) -> dict[str, WhatsAppProvider]:
    """Adapters keyed by `channels.provider`. A channel whose provider is missing is skipped.

    - `fake` runs only in development and test.
    - `openwa` is P5-T2, blocked until the live validation Go (V-01).
    - `meta_cloud` is interface-only (AD-06) and never registered.
    """
    providers: dict[str, WhatsAppProvider] = {}
    if settings.env in {"development", "test"}:
        providers["fake"] = FakeProvider()
    return providers
