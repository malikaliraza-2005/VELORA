"""The menu structure and its validation rules (P4-T1, P4-T2).

CSV import and form entry both produce a `Menu` and both go through `validate_menu`, so one
set of rules guards every way a menu gets in. Prices are integer paisa and never negative.
SKUs are the stable identity of an item across versions and are unique within a menu.

A `source_line` on an entry is the CSV line it came from, so a problem can be reported
against that line. Form entry leaves it unset and problems carry a path instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SKU_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,39}$")
# Rs 1,000,000; far above any menu price, low enough to catch a typo'd extra digit or two.
MAX_PRICE_PAISA = 100_000_000
MAX_CATEGORIES = 100
MAX_ITEMS = 1000
MAX_GROUPS_PER_ITEM = 20
MAX_OPTIONS_PER_GROUP = 50
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Option:
    name: str
    price_delta_paisa: int
    source_line: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ModifierGroup:
    name: str
    min_select: int
    max_select: int
    options: tuple[Option, ...]
    source_line: int | None = field(default=None, compare=False)

    @property
    def required(self) -> bool:
        return self.min_select > 0


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    price_paisa: int
    description: str | None = None
    groups: tuple[ModifierGroup, ...] = ()
    source_line: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Category:
    name: str
    items: tuple[Item, ...]
    source_line: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Menu:
    categories: tuple[Category, ...]

    def items(self) -> list[Item]:
        return [item for category in self.categories for item in category.items]

    def item_count(self) -> int:
        return sum(len(c.items) for c in self.categories)


@dataclass(frozen=True)
class Problem:
    """One validation failure. `line` is set for CSV input, `path` for form input."""

    message: str
    line: int | None = None
    path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.line, "path": self.path, "message": self.message}


def format_rupees(paisa: int) -> str:
    rupees, rest = divmod(paisa, 100)
    return f"Rs {rupees:,}" + (f".{rest:02d}" if rest else "")


class _Checker:
    def __init__(self) -> None:
        self.problems: list[Problem] = []

    def add(self, message: str, line: int | None, path: str) -> None:
        self.problems.append(Problem(message, line, None if line is not None else path))

    def text(
        self,
        value: str,
        label: str,
        max_len: int,
        line: int | None,
        path: str,
        *,
        optional: bool = False,
    ) -> None:
        if not value:
            if not optional:
                self.add(f"{label} is required", line, path)
            return
        if value != value.strip():
            self.add(f"{label} has spaces at the start or end", line, path)
        if len(value) > max_len:
            self.add(f"{label} is longer than {max_len} characters", line, path)
        if _CONTROL.search(value):
            self.add(f"{label} contains a line break or control character", line, path)

    def price(self, value: int, label: str, line: int | None, path: str) -> None:
        if value < 0:
            self.add(f"{label} can't be negative", line, path)
        elif value > MAX_PRICE_PAISA:
            self.add(f"{label} is above {format_rupees(MAX_PRICE_PAISA)}", line, path)


def validate_menu(menu: Menu, *, for_publish: bool = False) -> list[Problem]:
    """Every rule a menu must meet. With `for_publish`, also the rules for going live."""
    check = _Checker()
    if len(menu.categories) > MAX_CATEGORIES:
        check.add(f"a menu has at most {MAX_CATEGORIES} categories", None, "categories")
    if menu.item_count() > MAX_ITEMS:
        check.add(f"a menu has at most {MAX_ITEMS} items", None, "categories")
    if for_publish and menu.item_count() == 0:
        check.add("a menu needs at least one item before it can be published", None, "categories")

    categories: dict[str, int | None] = {}
    skus: dict[str, int | None] = {}
    for ci, category in enumerate(menu.categories):
        cpath = f"categories[{ci}]"
        line = category.source_line
        check.text(category.name, "category name", 80, line, f"{cpath}.name")
        key = category.name.casefold()
        if key in categories:
            check.add(f"category {category.name!r} appears twice", line, f"{cpath}.name")
        categories.setdefault(key, line)
        if for_publish and not category.items:
            check.add(f"category {category.name!r} has no items", line, f"{cpath}.items")

        for ii, item in enumerate(category.items):
            ipath = f"{cpath}.items[{ii}]"
            line = item.source_line
            if not SKU_PATTERN.match(item.sku):
                check.add(
                    "SKU must be 1 to 40 capital letters, digits, '-' or '_', "
                    "starting with a letter or digit",
                    line,
                    f"{ipath}.sku",
                )
            elif item.sku in skus:
                first = skus[item.sku]
                where = f" (first used on line {first})" if first is not None else ""
                check.add(f"duplicate SKU {item.sku}{where}", line, f"{ipath}.sku")
            skus.setdefault(item.sku, line)
            check.text(item.name, "item name", 120, line, f"{ipath}.name")
            if item.description is not None:
                check.text(item.description, "description", 500, line, f"{ipath}.description")
            check.price(item.price_paisa, "price", line, f"{ipath}.price_paisa")
            _check_groups(check, item, ipath)
    return check.problems


def _check_groups(check: _Checker, item: Item, ipath: str) -> None:
    if len(item.groups) > MAX_GROUPS_PER_ITEM:
        check.add(
            f"an item has at most {MAX_GROUPS_PER_ITEM} modifier groups",
            item.source_line,
            f"{ipath}.groups",
        )
    names: set[str] = set()
    for gi, group in enumerate(item.groups):
        gpath = f"{ipath}.groups[{gi}]"
        line = group.source_line
        check.text(group.name, "modifier group name", 60, line, f"{gpath}.name")
        if group.name.casefold() in names:
            check.add(f"{item.sku} has two modifier groups called {group.name!r}", line, gpath)
        names.add(group.name.casefold())
        count = len(group.options)
        if count == 0:
            check.add(f"modifier group {group.name!r} has no options", line, f"{gpath}.options")
        elif count > MAX_OPTIONS_PER_GROUP:
            check.add(
                f"a modifier group has at most {MAX_OPTIONS_PER_GROUP} options",
                line,
                f"{gpath}.options",
            )
        if group.min_select < 0 or group.max_select < 1 or group.min_select > group.max_select:
            check.add(
                "choices must satisfy 0 <= min <= max and max >= 1", line, f"{gpath}.max_select"
            )
        elif count and group.max_select > count:
            check.add(
                f"max choices ({group.max_select}) is more than the {count} options offered",
                line,
                f"{gpath}.max_select",
            )
        options: set[str] = set()
        for oi, option in enumerate(group.options):
            opath = f"{gpath}.options[{oi}]"
            oline = option.source_line
            check.text(option.name, "option name", 60, oline, f"{opath}.name")
            if option.name.casefold() in options:
                check.add(f"option {option.name!r} appears twice in {group.name!r}", oline, opath)
            options.add(option.name.casefold())
            check.price(
                option.price_delta_paisa, "option price", oline, f"{opath}.price_delta_paisa"
            )


# Price changes (audited on save and on publish) ------------------------------------------


def price_points(menu: Menu) -> dict[str, int]:
    """Every price in a menu, keyed `SKU` for items and `SKU/Group/Option` for options."""
    points: dict[str, int] = {}
    for item in menu.items():
        points[item.sku] = item.price_paisa
        for group in item.groups:
            for option in group.options:
                points[f"{item.sku}/{group.name}/{option.name}"] = option.price_delta_paisa
    return points


def price_changes(before: Menu | None, after: Menu) -> dict[str, Any]:
    """What a change from `before` to `after` does to prices, for the audit log and review."""
    old = price_points(before) if before is not None else {}
    new = price_points(after)
    old_skus = {i.sku for i in before.items()} if before is not None else set()
    new_skus = {i.sku for i in after.items()}
    return {
        "added_skus": sorted(new_skus - old_skus),
        "removed_skus": sorted(old_skus - new_skus),
        "price_changes": [
            {"key": key, "from_paisa": old[key], "to_paisa": new[key]}
            for key in sorted(old.keys() & new.keys())
            if old[key] != new[key]
        ],
    }
