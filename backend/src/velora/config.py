"""Settings loaded from environment variables.

Real values live only in the environment (or an uncommitted `.env`); the repository holds
nothing but `.env.example` with placeholders.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

VALID_ENVS = frozenset({"development", "test", "staging", "production"})
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
VALID_LOG_FORMATS = frozenset({"json", "text"})
PLACEHOLDER = "CHANGE_ME"
DEFAULT_AI_MODEL = "openai/gpt-oss-20b"
DEFAULT_AI_TIMEOUT_SECONDS = 8.0


def _optional(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name, "").strip()
    return None if not value or value == PLACEHOLDER else value


@dataclass(frozen=True)
class Settings:
    env: str
    log_level: str
    # json (default): one redacted JSON object per line (P10-T1). text: readable, for local use.
    log_format: Literal["json", "text"] = "json"
    # Runtime role (app_rw). None leaves the staff API unavailable (503), e.g. in unit tests.
    database_url: str | None = None
    # Worker work discovery (velora_dispatcher, P5). Only `python -m velora.workers` uses it.
    dispatcher_database_url: str | None = None
    # Origin of the staff dashboard. Magic links point here, and state-changing requests
    # that carry an Origin header must come from it.
    public_base_url: str = "http://localhost:8000"
    # Groq API key for AI proposals (P6-T4, AD-30). None: no AI; the numbered menu only.
    groq_api_key: str | None = field(default=None, repr=False)
    ai_model: str = DEFAULT_AI_MODEL
    # After this long the AI is abandoned and the customer gets the numbered menu.
    ai_timeout_seconds: float = DEFAULT_AI_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        env = source.get("VELORA_ENV", "development").strip().lower()
        log_level = source.get("VELORA_LOG_LEVEL", "INFO").strip().upper()
        log_format = source.get("VELORA_LOG_FORMAT", "json").strip().lower() or "json"
        if log_format not in VALID_LOG_FORMATS:
            raise ValueError(f"VELORA_LOG_FORMAT must be json or text, got {log_format!r}")
        if env not in VALID_ENVS:
            raise ValueError(f"VELORA_ENV must be one of {sorted(VALID_ENVS)}, got {env!r}")
        if log_level not in VALID_LOG_LEVELS:
            raise ValueError(
                f"VELORA_LOG_LEVEL must be one of {sorted(VALID_LOG_LEVELS)}, got {log_level!r}"
            )
        configured = _optional(source, "VELORA_PUBLIC_BASE_URL")
        base_url = (configured or cls.public_base_url).rstrip("/")
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.path:
            raise ValueError(f"VELORA_PUBLIC_BASE_URL must be an origin, got {base_url!r}")
        if configured and env in {"staging", "production"} and parts.scheme != "https":
            raise ValueError("VELORA_PUBLIC_BASE_URL must use https in staging and production")
        timeout_text = _optional(source, "VELORA_AI_TIMEOUT_SECONDS")
        try:
            timeout = DEFAULT_AI_TIMEOUT_SECONDS if timeout_text is None else float(timeout_text)
        except ValueError:
            timeout = -1.0
        if not 0.5 <= timeout <= 30:
            raise ValueError("VELORA_AI_TIMEOUT_SECONDS must be a number from 0.5 to 30")
        return cls(
            env=env,
            log_level=log_level,
            log_format="text" if log_format == "text" else "json",
            database_url=_optional(source, "VELORA_DATABASE_URL"),
            dispatcher_database_url=_optional(source, "VELORA_DISPATCHER_DATABASE_URL"),
            public_base_url=base_url,
            groq_api_key=_optional(source, "VELORA_GROQ_API_KEY"),
            ai_model=_optional(source, "VELORA_AI_MODEL") or DEFAULT_AI_MODEL,
            ai_timeout_seconds=timeout,
        )

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.public_base_url)
        return f"{parts.scheme}://{parts.netloc}"

    def magic_link_url(self, token: str) -> str:
        # The token travels in the fragment, which browsers never send to servers or proxies,
        # so it stays out of access logs. The dashboard page posts it to /api/auth/login.
        return f"{self.public_base_url}/login#{token}"
