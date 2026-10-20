"""FastAPI application factory (ingress service)."""

from __future__ import annotations

from fastapi import FastAPI

from velora import __version__
from velora.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="VELORA", version=__version__)
    app.state.settings = settings

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
