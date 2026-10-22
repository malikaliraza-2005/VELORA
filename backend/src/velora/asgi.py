"""The ASGI entry point for the API and dashboard: `uvicorn velora.asgi:app` (P9).

This is a composition root, like `velora.workers.__main__`: it may wire provider adapters in
through the registry. The production server and its settings are P12.
"""

from __future__ import annotations

from velora.app import create_app
from velora.config import Settings
from velora.messaging.registry import build_providers
from velora.redaction import configure_logging

settings = Settings.from_env()
configure_logging(settings.log_level)
app = create_app(settings, providers=build_providers(settings))
