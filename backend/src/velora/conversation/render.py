"""Rendering menus, carts and the order summary as WhatsApp text (P6-T3, P6-T5).

- **Menus** are numbered text. The provider interface has no list or button messages yet, so
  this is what every provider gets, OpenWA included (F7); `Capabilities.lists` and `.buttons`
  are not used.
- **Splitting:** a reply longer than the limit is split on line breaks into several messages.
  The limit is the provider's `max_text_length`, capped at `SPLIT_LIMIT`. That cap is
  **provisional until V-09** measures what OpenWA delivers well.
- **Amounts** come only from `QuotedLine` and `Quote`, which the server computed from the
  published menu and settings (AD-20). The summary is this fixed template; no AI text is ever
  part of it.
"""

from __future__ import annotations

from velora.conversation import copy
from velora.menu.model import Category, Item, Menu, ModifierGroup, format_rupees
from velora.messaging.provider import Capabilities
from velora.ordering.pricing import Quote, QuotedLine

# Provisional (V-09): the longest single message we send, in characters.
SPLIT_LIMIT = 1000


def split_limit(capabilities: Capabilities) -> int:
    return max(1, min(capabilities.max_text_length, SPLIT_LIMIT))


def split(text: str, limit: int) -> list[str]:
    """Split on line breaks into messages of at most `limit` characters. A single line
    longer than the limit is cut on a space where possible."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit + 1)
            cut = cut if cut > 0 else limit
            piece, line = line[:cut].rstrip(), line[cut:].lstrip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece)
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = line
    if current.strip():
        chunks.append(current)
    return [c.strip("\n") for c in chunks if c.strip()]


def top_menu(menu: Menu) -> str:
    lines = ["Menu — reply with a number:"]
    lines += [f"{n}. {c.name}" for n, c in enumerate(menu.categories, 1)]
    lines.append(copy.MENU_FOOTER)
    return "\n".join(lines)


def category(cat: Category) -> str:
    lines = [cat.name]
    lines += [
        f"{n}. {item.name} — {format_rupees(item.price_paisa)}"
        for n, item in enumerate(cat.items, 1)
    ]
    lines.append(copy.MENU_HINT)
    return "\n".join(lines)


def group_question(item: Item, group: ModifierGroup) -> str:
    lines = [f"{group.name} for {item.name}?"]
    for n, option in enumerate(group.options, 1):
        extra = f" (+{format_rupees(option.price_delta_paisa)})" if option.price_delta_paisa else ""
        lines.append(f"{n}. {option.name}{extra}")
    if group.max_select > 1:
        lines.append(f'You can choose up to {group.max_select}, e.g. "1,2".')
    if group.min_select == 0:
        lines.append("Reply SKIP for none.")
    return "\n".join(lines)


def line_text(line: QuotedLine) -> str:
    options = ", ".join(option for _, option in line.choices)
    name = f"{line.name} ({options})" if options else line.name
    return f"{line.quantity} × {name} — {format_rupees(line.line_total_paisa)}"


def added(lines: tuple[QuotedLine, ...]) -> str:
    return "\n".join(["Added: " + line_text(line) for line in lines] + [copy.ADDED_HINT])


def proposal(lines: tuple[QuotedLine, ...], notes: list[str]) -> str:
    body = ["I understood:"] + [f"• {line_text(line)}" for line in lines]
    body += notes
    body.append(copy.PROPOSAL_HINT)
    return "\n".join(body)


def cart(lines: tuple[QuotedLine, ...], notes: list[str]) -> str:
    body = ["🛒 Your cart:"]
    body += [f"{n}. {line_text(line)}" for n, line in enumerate(lines, 1)]
    body.append(f"Subtotal: {format_rupees(sum(line.line_total_paisa for line in lines))}")
    body += notes
    body.append(
        'Reply CHECKOUT to order, MENU to add more, "remove 1" to remove a line, '
        "or CANCEL to clear the cart."
    )
    return "\n".join(body)


def numbered(question: str, options: list[str]) -> str:
    return "\n".join([question] + [f"{n}. {o}" for n, o in enumerate(options, 1)])


def summary(
    quote: Quote,
    *,
    address: str | None,
    delivery_note: str | None,
    pickup_at_text: str | None,
) -> str:
    """The order summary, from the server's quote only (A6)."""
    body = ["🧾 Order summary — please check"]
    body += [line_text(line) for line in quote.lines]
    if quote.fulfilment == "delivery":
        body.append(f"Delivery — {format_rupees(quote.delivery_fee_paisa)}")
    rate = f"{quote.tax_bps / 100:g}%"
    body.append(
        f"{copy.TAX_LABELS[quote.payment_method]} {rate} — {format_rupees(quote.tax_paisa)}"
    )
    body.append("─────────────")
    body.append(f"TOTAL — {format_rupees(quote.total_paisa)}")
    body.append(f"Payment: {copy.PAYMENT_LABELS[quote.payment_method]}")
    if quote.fulfilment == "delivery":
        body.append(f"Deliver to: {address}")
        if delivery_note:
            body.append(f"Note: {delivery_note}")
    else:
        body.append(f"Takeaway, pickup at {pickup_at_text}")
    body.append("")
    body.append(copy.SUMMARY_FOOTER)
    return "\n".join(body)
