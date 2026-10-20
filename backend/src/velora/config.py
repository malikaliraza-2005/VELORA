"""Settings loaded from environment variables.

Real values live only in the environment (or an uncommitted `.env`); the repository holds
nothing but `.env.example` with placeholders.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

VALID_ENVS = frozenset({"development", "test", "staging", "production"})
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


@dataclass(frozen=True)
class Settings:
    env: str
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        env = source.get("VELORA_ENV", "development").strip().lower()
        log_level = source.get("VELORA_LOG_LEVEL", "INFO").strip().upper()
        if env not in VALID_ENVS:
            raise ValueError(f"VELORA_ENV must be one of {sorted(VALID_ENVS)}, got {env!r}")
        if log_level not in VALID_LOG_LEVELS:
            raise ValueError(
                f"VELORA_LOG_LEVEL must be one of {sorted(VALID_LOG_LEVELS)}, got {log_level!r}"
            )
        return cls(env=env, log_level=log_level)
