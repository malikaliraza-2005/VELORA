"""Composition root for AI proposals (P6-T4): which model adapter this deployment runs.

Only this module and the entry points that call it may import `velora.ai.groq` (a static
check enforces it).
"""

from __future__ import annotations

from velora.ai.groq import GroqProposer
from velora.ai.proposer import Proposer
from velora.config import Settings


def build_proposer(settings: Settings) -> Proposer | None:
    """The configured proposer, or None when no key is set (numbered menu only)."""
    if settings.groq_api_key is None:
        return None
    return GroqProposer(
        settings.groq_api_key, model=settings.ai_model, timeout=settings.ai_timeout_seconds
    )
