"""CSV menu import (P4-T2). Invalid files are rejected with line-level errors.

One row per item or per modifier option, UTF-8 (Urdu script is fine), header on line 1:

    kind,category,sku,name,price,description,group,min,max
    item,Burgers,ZINGER,Zinger Burger,650,Crispy chicken fillet,,,
    option,,ZINGER,Regular,0,,Size,1,1
    option,,ZINGER,Large,150,,Size,,

- `kind` is `item` or `option`.
- `price` is in rupees, e.g. `650` or `650.50`. For an option it is the extra charge.
- An option row names its item by `sku`; the item row must come first. `group` is the
  modifier group. `min` and `max` (how many options a customer picks) are required on the
  group's first row and, if repeated, must match.
- SKUs are case-insensitive and stored in capitals.

The whole file is checked and every problem is reported; nothing is saved unless it is clean.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from velora.menu.model import Category, Item, Menu, ModifierGroup, Option, Problem, validate_menu

REQUIRED_COLUMNS = ("kind", "category", "sku", "name", "price")
OPTIONAL_COLUMNS = ("description", "group", "min", "max")
MAX_BYTES = 1_000_000
MAX_ROWS = 5000
_PRICE = re.compile(r"^\d{1,7}(\.\d{1,2})?$")
_COUNT = re.compile(r"^\d{1,3}$")

_Bad = Callable[[int | None, str], None]


class CsvImportError(ValueError):
    def __init__(self, problems: list[Problem]) -> None:
        super().__init__(f"the menu file has {len(problems)} problem(s)")
        self.problems = problems


@dataclass
class _Group:
    name: str
    min_select: int
    max_select: int
    line: int
    options: list[Option] = field(default_factory=list)


@dataclass
class _Item:
    sku: str
    name: str
    price_paisa: int
    description: str | None
    line: int
    groups: dict[str, _Group] = field(default_factory=dict)


def parse_rupees(value: str) -> int:
    """`650` or `650.50` rupees to paisa. Raises ValueError with a message for the user."""
    if value.startswith("-"):
        raise ValueError("can't be negative")
    if not _PRICE.match(value):
        raise ValueError("must be a number of rupees, like 650 or 650.50 (no commas)")
    return int(Decimal(value) * 100)


def parse_menu_csv(data: bytes | str) -> Menu:
    """Parse and validate a menu CSV. Raises `CsvImportError` listing every problem."""
    problems: list[Problem] = []

    def bad(line: int | None, message: str) -> None:
        problems.append(Problem(message, line))

    if isinstance(data, bytes):
        if len(data) > MAX_BYTES:
            raise CsvImportError([Problem(f"the file is larger than {MAX_BYTES:,} bytes")])
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CsvImportError([Problem("the file must be saved as UTF-8 text")]) from exc
    else:
        text = data.removeprefix("﻿")
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise CsvImportError([Problem(f"the file is larger than {MAX_BYTES:,} bytes")])

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        raise CsvImportError([Problem("the file is empty", 1)]) from None
    except csv.Error as exc:
        raise CsvImportError([Problem(f"not a readable CSV file ({exc})", 1)]) from exc
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    unknown = [c for c in header if c not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
    duplicated = sorted({c for c in header if header.count(c) > 1})
    if missing:
        bad(1, "missing column(s): " + ", ".join(missing))
    if unknown:
        bad(1, "unknown column(s): " + ", ".join(unknown))
    if duplicated:
        bad(1, "repeated column(s): " + ", ".join(duplicated))
    if problems:
        raise CsvImportError(problems)

    categories: dict[str, list[_Item]] = {}
    category_lines: dict[str, int] = {}
    items: dict[str, _Item] = {}
    # Every SKU on an item row and its line, including rows rejected for other reasons.
    seen: dict[str, int] = {}
    rows = 0
    try:
        while True:
            # A quoted value may span lines; report the line the row starts on.
            line = reader.line_num + 1
            record = next(reader, None)
            if record is None:
                break
            if not any(cell.strip() for cell in record):
                continue
            rows += 1
            if rows > MAX_ROWS:
                bad(line, f"the file has more than {MAX_ROWS} rows")
                break
            if len(record) != len(header):
                bad(line, f"expected {len(header)} values, found {len(record)}")
                continue
            row = {name: cell.strip() for name, cell in zip(header, record, strict=True)}
            kind = row["kind"].lower()
            if kind == "item":
                _item_row(row, line, bad, categories, category_lines, items, seen)
            elif kind == "option":
                _option_row(row, line, bad, items, seen)
            else:
                bad(line, "kind must be 'item' or 'option'")
    except csv.Error as exc:
        bad(line, f"not a readable CSV row ({exc})")

    menu = Menu(
        tuple(
            Category(
                name,
                tuple(
                    Item(
                        sku=i.sku,
                        name=i.name,
                        price_paisa=i.price_paisa,
                        description=i.description,
                        groups=tuple(_group(g) for g in i.groups.values()),
                        source_line=i.line,
                    )
                    for i in members
                ),
                category_lines[name],
            )
            for name, members in categories.items()
        )
    )
    problems += validate_menu(menu)
    if problems:
        problems.sort(key=lambda p: p.line or 0)
        raise CsvImportError(problems)
    return menu


def _group(g: _Group) -> ModifierGroup:
    return ModifierGroup(g.name, g.min_select, g.max_select, tuple(g.options), g.line)


def _price(row: dict[str, str], line: int, bad: _Bad, label: str) -> int | None:
    value = row["price"]
    if not value:
        bad(line, f"{label} is required")
        return None
    try:
        return parse_rupees(value)
    except ValueError as exc:
        bad(line, f"{label} {exc}")
        return None


def _item_row(
    row: dict[str, str],
    line: int,
    bad: _Bad,
    categories: dict[str, list[_Item]],
    category_lines: dict[str, int],
    items: dict[str, _Item],
    seen: dict[str, int],
) -> None:
    filled = [column for column in ("group", "min", "max") if row.get(column)]
    if filled:
        bad(line, f"an item row leaves {', '.join(filled)} empty (modifiers go on option rows)")
    category = row["category"]
    sku = row["sku"].upper()
    if not category:
        bad(line, "category is required")
    if not sku:
        bad(line, "SKU is required")
    elif sku in seen:
        bad(line, f"duplicate SKU {sku} (first used on line {seen[sku]})")
        return
    else:
        seen[sku] = line
    price = _price(row, line, bad, "price")
    if filled or not category or not sku or price is None:
        return
    item = _Item(sku, row["name"], price, row.get("description") or None, line)
    items[sku] = item
    # Categories match regardless of case; the first spelling wins.
    key = next((c for c in categories if c.casefold() == category.casefold()), category)
    categories.setdefault(key, []).append(item)
    category_lines.setdefault(key, line)


def _option_row(
    row: dict[str, str], line: int, bad: _Bad, items: dict[str, _Item], seen: dict[str, int]
) -> None:
    sku = row["sku"].upper()
    group_name = row.get("group", "")
    if row["category"] or row.get("description"):
        bad(line, "an option row leaves 'category' and 'description' empty")
    if not sku:
        bad(line, "SKU is required (the item this option belongs to)")
        return
    item = items.get(sku)
    if item is None:
        if sku not in seen:  # otherwise the item row's own problem is already reported
            bad(line, f"no item with SKU {sku} above this line; put the item row first")
        return
    if not group_name:
        bad(line, "group is required on an option row")
        return
    price = _price(row, line, bad, "option price")
    min_raw, max_raw = row.get("min", ""), row.get("max", "")
    for label, raw in (("min", min_raw), ("max", max_raw)):
        if raw and not _COUNT.match(raw):
            bad(line, f"{label} must be a whole number")
            return

    group = next((g for k, g in item.groups.items() if k.casefold() == group_name.casefold()), None)
    if group is None:
        if not min_raw or not max_raw:
            bad(line, f"the first row of group {group_name!r} needs both min and max")
            return
        group = _Group(group_name, int(min_raw), int(max_raw), line)
        item.groups[group_name] = group
    elif (min_raw and int(min_raw) != group.min_select) or (
        max_raw and int(max_raw) != group.max_select
    ):
        bad(line, f"min/max differ from group {group_name!r} on line {group.line}")
        return
    if price is not None:
        group.options.append(Option(row["name"], price, line))
