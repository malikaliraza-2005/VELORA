"""Offline AI evaluation harness (P6-T7). Not a CI gate.

    python -m velora.ai.evaluate evaluation/synthetic-a15.json

It runs the configured proposer (`VELORA_GROQ_API_KEY`, `VELORA_AI_MODEL`) over every case in
a dataset and reports the share of correct proposals. Requests are built exactly as in
production (`build_request`, so the same minimisation applies).

A case is correct when the intent is one of the accepted intents and, for orders, the
proposed lines equal the expected ones: same SKUs, quantities and option choices, in any
order. The pilot target is at least 90% (`TARGET`), an **estimate the owner has to confirm**,
measured on the P0 utterance set of real, consented messages (P0-F6). The bundled dataset is
synthetic (MVP scope A15): a first sanity check, not a model selection.

This is a composition root: it may import model adapters through `velora.ai.registry`.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from velora.ai.proposer import (
    Proposal,
    Proposer,
    ProposerError,
    RateLimitedError,
    build_request,
)
from velora.ai.registry import build_proposer
from velora.config import Settings
from velora.menu.model import Category, Item, Menu, ModifierGroup, Option

TARGET = 0.90

LineKey = tuple[str, int, frozenset[tuple[str, str]]]


@dataclass(frozen=True)
class Case:
    text: str
    intents: frozenset[str]
    lines: Counter[LineKey]


@dataclass
class Result:
    case: Case
    correct: bool
    got: Proposal | None
    error: str | None
    seconds: float


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    @property
    def correct(self) -> int:
        return sum(r.correct for r in self.results)

    @property
    def accuracy(self) -> float:
        return self.correct / len(self.results) if self.results else 0.0


def _key(sku: str, quantity: int, choices: Sequence[Sequence[str]]) -> LineKey:
    return (
        sku.upper(),
        quantity,
        frozenset((g.casefold(), o.casefold()) for g, o in choices),
    )


def load_dataset(path: Path) -> tuple[Menu, list[Case]]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    menu = Menu(
        tuple(
            Category(
                c["name"],
                tuple(
                    Item(
                        i["sku"],
                        i["name"],
                        i["price_paisa"],
                        groups=tuple(
                            ModifierGroup(
                                g["name"],
                                g["min_select"],
                                g["max_select"],
                                tuple(
                                    Option(o["name"], o["price_delta_paisa"]) for o in g["options"]
                                ),
                            )
                            for g in i.get("groups", [])
                        ),
                    )
                    for i in c["items"]
                ),
            )
            for c in data["menu"]
        )
    )
    cases = [
        Case(
            c["text"],
            frozenset(c["intents"]),
            Counter(
                _key(ln["sku"], ln["quantity"], ln.get("choices", [])) for ln in c.get("lines", [])
            ),
        )
        for c in data["cases"]
    ]
    return menu, cases


def is_correct(case: Case, got: Proposal) -> bool:
    if got.intent not in case.intents:
        return False
    if got.intent != "order":
        return True
    return Counter(_key(ln.sku, ln.quantity, ln.choices) for ln in got.lines) == case.lines


async def evaluate(
    proposer: Proposer,
    menu: Menu,
    cases: list[Case],
    *,
    timeout: float,
    pause: float = 0.0,
    retries: int = 3,
    rate_wait: float = 20.0,
) -> Report:
    """Run every case. `pause` seconds between cases keeps a free tier's per-minute token
    limit; a rate-limited case waits (Retry-After, else `rate_wait`) and is retried."""
    report = Report()
    for n, case in enumerate(cases):
        if n and pause:
            await asyncio.sleep(pause)
        started = time.monotonic()
        got: Proposal | None = None
        error: str | None = None
        for attempt in range(retries + 1):
            try:
                request = build_request(case.text, menu)
                got = await asyncio.wait_for(proposer.propose(request), timeout)
                error = None
                break
            except RateLimitedError as exc:
                error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    await asyncio.sleep(exc.retry_after or rate_wait)
            except (ProposerError, TimeoutError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                break
        correct = got is not None and is_correct(case, got)
        report.results.append(Result(case, correct, got, error, time.monotonic() - started))
    return report


def render_report(report: Report) -> str:
    lines = []
    for r in report.results:
        mark = "ok  " if r.correct else "MISS"
        got = r.error or (
            "-"
            if r.got is None
            else f"{r.got.intent} {[(ln.sku, ln.quantity, list(ln.choices)) for ln in r.got.lines]}"
        )
        lines.append(f"{mark} {r.seconds:5.2f}s  {r.case.text!r} -> {got}")
    lines.append(
        f"\nCorrect: {report.correct}/{len(report.results)} = {report.accuracy:.0%} "
        f"(target {TARGET:.0%}, an estimate to be confirmed; not a CI gate)"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline AI proposal evaluation (P6-T7).")
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--pause",
        type=float,
        default=8.0,
        help="seconds between cases, to stay under a free tier's tokens-per-minute limit",
    )
    args = parser.parse_args(argv)
    # Datasets hold Urdu script; a Windows console defaults to a code page without it.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings.from_env()
    proposer = build_proposer(settings)
    if proposer is None:
        print("Set VELORA_GROQ_API_KEY to run the evaluation.", file=sys.stderr)
        return 2
    menu, cases = load_dataset(args.dataset)
    report = asyncio.run(
        evaluate(proposer, menu, cases, timeout=settings.ai_timeout_seconds, pause=args.pause)
    )
    print(f"Model: {settings.ai_model}")
    print(render_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
