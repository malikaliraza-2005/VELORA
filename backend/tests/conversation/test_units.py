"""Units for P6: pickup-time parsing, AI settings and the offline evaluation harness (T7)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from velora.ai.evaluate import TARGET, evaluate, is_correct, load_dataset, render_report
from velora.ai.proposer import (
    Proposal,
    ProposalRequest,
    ProposedLine,
    ProposerError,
    RateLimitedError,
)
from velora.config import Settings
from velora.conversation.pktime import PKT, format_time, parse_pickup
from velora.menu.model import validate_menu

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "synthetic-a15.json"
# 7:00 PM in Pakistan.
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "local"),
    [
        ("30 min", (19, 30)),
        ("45", (19, 45)),
        ("8:30 pm", (20, 30)),
        ("8.30PM", (20, 30)),
        ("21:00", (21, 0)),
        ("9 baje", (21, 0)),  # 9 in the evening comes round before 9 tomorrow morning
        ("8", (20, 0)),
        ("11 am", (11, 0)),  # tomorrow
        ("12 pm", (12, 0)),
    ],
)
def test_parse_pickup_reads_typed_times(text: str, local: tuple[int, int]) -> None:
    at = parse_pickup(text, NOW)
    assert at is not None
    assert (at.astimezone(PKT).hour, at.astimezone(PKT).minute) == local
    assert NOW < at <= NOW + timedelta(days=1)


@pytest.mark.parametrize("text", ["tomorrow", "25:00", "13 pm", "8:75", "in 3 days", ""])
def test_parse_pickup_refuses_what_is_not_a_time(text: str) -> None:
    assert parse_pickup(text, NOW) is None


def test_format_time_shows_pakistan_time() -> None:
    assert format_time(NOW) == "7:00 PM"


def test_ai_settings_come_from_the_environment_and_hide_the_key() -> None:
    settings = Settings.from_env(
        {"VELORA_GROQ_API_KEY": "gsk-secret", "VELORA_AI_TIMEOUT_SECONDS": "5"}
    )
    assert settings.groq_api_key == "gsk-secret"
    assert settings.ai_timeout_seconds == 5
    assert settings.ai_model == "openai/gpt-oss-20b"
    assert "gsk-secret" not in repr(settings)
    assert Settings.from_env({"VELORA_GROQ_API_KEY": "CHANGE_ME"}).groq_api_key is None
    for bad in ("0", "60", "soon"):
        with pytest.raises(ValueError, match="VELORA_AI_TIMEOUT_SECONDS"):
            Settings.from_env({"VELORA_AI_TIMEOUT_SECONDS": bad})


# T7: the offline evaluation harness (the accuracy itself is not a CI gate) ----------------


def test_the_bundled_dataset_is_a_valid_menu_with_known_skus() -> None:
    menu, cases = load_dataset(DATASET)
    assert validate_menu(menu, for_publish=True) == []
    skus = {item.sku for item in menu.items()}
    assert len(cases) == 20
    for case in cases:
        assert {key[0] for key in case.lines} <= skus


class Oracle:
    """Answers every case correctly, except the ones named in `wrong`."""

    def __init__(self, path: Path, wrong: frozenset[str] = frozenset()) -> None:
        _, cases = load_dataset(path)
        self.cases = {c.text: c for c in cases}
        self.wrong = wrong

    async def propose(self, request: ProposalRequest) -> Proposal:
        case = next(
            c for t, c in self.cases.items() if request.text == t or request.text.startswith(t[:10])
        )
        if case.text in self.wrong:
            raise ProposerError("simulated failure")
        intent = sorted(case.intents)[0]
        lines = tuple(
            ProposedLine(sku, quantity, tuple(sorted(choices)))
            for (sku, quantity, choices), count in case.lines.items()
            for _ in range(count)
        )
        return Proposal(intent, lines)  # type: ignore[arg-type]


def test_the_harness_scores_correct_and_failed_cases() -> None:
    menu, cases = load_dataset(DATASET)
    perfect = asyncio.run(evaluate(Oracle(DATASET), menu, cases, timeout=1))
    # The oracle's choices are case-folded; the scorer compares case-insensitively.
    assert perfect.accuracy == 1.0
    wrong = frozenset({"10 shawarma", "menu dikhao"})
    report = asyncio.run(evaluate(Oracle(DATASET, wrong), menu, cases, timeout=1))
    assert report.correct == 18
    text = render_report(report)
    assert f"18/20 = 90% (target {TARGET:.0%}" in text
    assert "MISS" in text and "simulated failure" in text


def test_is_correct_needs_the_intent_and_the_exact_lines() -> None:
    _, cases = load_dataset(DATASET)
    first = cases[0]  # 2 zinger large aur 1 fries
    right = Proposal(
        "order", (ProposedLine("FRIES", 1), ProposedLine("ZINGER", 2, (("size", "LARGE"),)))
    )
    assert is_correct(first, right)
    assert not is_correct(
        first, Proposal("order", (ProposedLine("ZINGER", 2, (("Size", "Large"),)),))
    )
    assert not is_correct(first, Proposal("menu"))
    injection = next(c for c in cases if c.text.startswith("ignore previous"))
    assert is_correct(injection, Proposal("unknown")) and is_correct(injection, Proposal("menu"))


class Throttled:
    """Rate-limited for the first `refusals` calls, then answers like the oracle."""

    def __init__(self, path: Path, refusals: int) -> None:
        self.oracle = Oracle(path)
        self.refusals = refusals
        self.calls = 0

    async def propose(self, request: ProposalRequest) -> Proposal:
        self.calls += 1
        if self.calls <= self.refusals:
            raise RateLimitedError("HTTP 429", retry_after=0)
        return await self.oracle.propose(request)


def test_the_harness_waits_and_retries_when_rate_limited() -> None:
    menu, cases = load_dataset(DATASET)
    proposer = Throttled(DATASET, refusals=2)
    report = asyncio.run(evaluate(proposer, menu, cases[:3], timeout=1, rate_wait=0))
    assert report.correct == 3
    assert proposer.calls == 5
    # Retries are bounded: a provider that always refuses gives a miss, not a hang.
    always = Throttled(DATASET, refusals=1000)
    report = asyncio.run(evaluate(always, menu, cases[:1], timeout=1, retries=2, rate_wait=0))
    assert report.correct == 0 and always.calls == 3
    assert "RateLimitedError" in render_report(report)
