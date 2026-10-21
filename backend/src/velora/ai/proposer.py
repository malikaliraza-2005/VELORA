"""The `Proposer` interface: free text in, a *proposal* out (P6-T4, AD-20).

What an AI model may do here, and nothing more:
- read the customer's text, minimised (`minimise`), and the offerable menu's names, SKUs and
  option names. It is never given the tenant, a channel, a phone number, an address, a
  customer name or any price;
- answer with an intent and proposed lines (SKU, quantity, option choices).

It has no tools and writes nothing. `parse_proposal` keeps only the fields below, within
fixed bounds, and drops everything else (prices, totals, ids, extra text), so a prompt
injection can at most change *which* items are proposed. The conversation engine then resolves
each line against the published menu and prices it on the server, and the customer sees only
names and amounts read from the database.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast, get_args

from velora.menu.model import Menu

Intent = Literal[
    "order", "menu", "status", "handoff", "cancel", "delivery_info", "payment_claim", "unknown"
]
INTENTS: frozenset[str] = frozenset(get_args(Intent))

MAX_TEXT = 500
MAX_LINES = 10
MAX_QUANTITY = 50
MAX_CHOICES = 10

# Phone numbers (and any other long digit run), e-mail addresses and links never reach the
# model. House numbers and quantities are short, so they survive.
_DIGIT_RUN = re.compile(r"\+?\d[\d\s-]{5,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_URL = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True)
class MenuEntry:
    sku: str
    name: str
    category: str
    # (group name, option names), for the model to name a choice.
    groups: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class ProposalRequest:
    """Everything the model sees. Deliberately no tenant, customer or price fields."""

    text: str
    menu: tuple[MenuEntry, ...]


@dataclass(frozen=True)
class ProposedLine:
    sku: str
    quantity: int
    choices: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Proposal:
    intent: Intent
    lines: tuple[ProposedLine, ...] = ()


class ProposerError(Exception):
    """The model could not be reached or answered with something unusable."""


class RateLimitedError(ProposerError):
    """The model's provider refused the request for its rate limit. A customer gets the
    numbered menu like any other error; the offline harness waits and retries."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Proposer(Protocol):
    async def propose(self, request: ProposalRequest) -> Proposal: ...


def minimise(text: str) -> str:
    """The customer's text as the model may see it."""
    text = _CONTROL.sub(" ", text)
    text = _EMAIL.sub("[email]", text)
    text = _URL.sub("[link]", text)
    text = _DIGIT_RUN.sub("[number]", text)
    return " ".join(text.split())[:MAX_TEXT]


def build_request(text: str, offerable: Menu) -> ProposalRequest:
    entries = tuple(
        MenuEntry(
            item.sku,
            item.name,
            category.name,
            tuple((g.name, tuple(o.name for o in g.options)) for g in item.groups),
        )
        for category in offerable.categories
        for item in category.items
    )
    return ProposalRequest(minimise(text), entries)


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if 0 < len(value) <= limit else None


def parse_proposal(raw: str | dict[str, Any]) -> Proposal:
    """Validate model output. Anything outside the expected shape is dropped, not trusted."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as exc:
        raise ProposerError("the model did not answer with JSON") from exc
    if not isinstance(data, dict):
        raise ProposerError("the model's answer is not a JSON object")
    found = data.get("intent")
    intent: Intent = cast(Intent, found) if found in INTENTS else "unknown"
    lines: list[ProposedLine] = []
    items = data.get("items")
    for item in items if isinstance(items, list) else []:
        if len(lines) == MAX_LINES:
            break
        if not isinstance(item, dict):
            continue
        sku = _text(item.get("sku"), 40)
        quantity = item.get("quantity", 1)
        if sku is None or isinstance(quantity, bool) or not isinstance(quantity, int):
            continue
        if not 1 <= quantity <= MAX_QUANTITY:
            continue
        choices: list[tuple[str, str]] = []
        raw_choices = item.get("choices")
        for choice in raw_choices if isinstance(raw_choices, list) else []:
            if len(choices) == MAX_CHOICES or not isinstance(choice, dict):
                continue
            group, option = _text(choice.get("group"), 60), _text(choice.get("option"), 60)
            if group is not None and option is not None:
                choices.append((group, option))
        lines.append(ProposedLine(sku.upper(), quantity, tuple(choices)))
    if intent == "order" and not lines:
        intent = "unknown"
    return Proposal(intent, tuple(lines) if intent == "order" else ())
