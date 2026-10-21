"""P4-T2: the import validator. Each rule rejects an invalid CSV with a line-level error."""

from __future__ import annotations

import pytest

from velora.menu.csv_import import CsvImportError, parse_menu_csv, parse_rupees
from velora.menu.model import Item, Menu, ModifierGroup, Option, validate_menu

HEADER = "kind,category,sku,name,price,description,group,min,max\n"
GOOD = (
    HEADER
    + "item,Burgers,zinger,Zinger Burger,650,Crispy,,,\n"
    + "option,,ZINGER,Regular,0,,Size,1,1\n"
    + "option,,ZINGER,Large,150.50,,Size,,\n"
    + "item,burgers,SMASH,Beef Smash Burger,890,,,,\n"
    + "item,Biryani,BIRYANI,چکن بریانی,450,,,,\n"
)


def problems(text: str | bytes) -> list[tuple[int | None, str]]:
    with pytest.raises(CsvImportError) as failed:
        parse_menu_csv(text)
    return [(p.line, p.message) for p in failed.value.problems]


def only(text: str) -> tuple[int | None, str]:
    found = problems(text)
    assert len(found) == 1, found
    return found[0]


def test_a_valid_file_parses() -> None:
    menu = parse_menu_csv(GOOD.encode("utf-8-sig"))
    assert [c.name for c in menu.categories] == ["Burgers", "Biryani"]
    zinger, smash = menu.categories[0].items
    assert (zinger.sku, zinger.price_paisa, zinger.description) == ("ZINGER", 65000, "Crispy")
    assert zinger.groups == (
        ModifierGroup("Size", 1, 1, (Option("Regular", 0), Option("Large", 15050))),
    )
    assert smash.sku == "SMASH"  # "burgers" joined the "Burgers" category
    assert menu.categories[1].items[0].name == "چکن بریانی"  # Urdu script is fine


def test_only_required_columns_are_needed() -> None:
    menu = parse_menu_csv("kind,category,sku,name,price\nitem,Drinks,COKE,Coca-Cola,120\n")
    assert menu.items()[0].price_paisa == 12000


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("item,Burgers,FREE,Free Burger,-5,,,,", "price can't be negative"),
        ("item,Burgers,X1,Burger,6,50,,,,", "expected 9 values"),
        ("item,Burgers,X1,Burger,1250.505,,,,", "price must be a number of rupees"),
        ("item,Burgers,X1,Burger,abc,,,,", "price must be a number of rupees"),
        ("item,Burgers,X1,Burger,,,,,", "price is required"),
        ("item,Burgers,X1,,100,,,,", "item name is required"),
        ("item,,X1,Burger,100,,,,", "category is required"),
        ("item,Burgers,,Burger,100,,,,", "SKU is required"),
        ("item,Burgers,BAD SKU,Burger,100,,,,", "SKU must be"),
        ("dish,Burgers,X1,Burger,100,,,,", "kind must be 'item' or 'option'"),
        ("item,Burgers,X1,Burger,100,,Size,1,1", "an item row leaves group, min, max empty"),
        ("item,Burgers,X1,Burger,2000000,,,,", "price is above"),
        ('item,Burgers,X1,"Two\nLines",100,,,,', "line break"),
    ],
)
def test_each_item_rule_reports_its_line(row: str, message: str) -> None:
    line, text = only(HEADER + "item,Drinks,COKE,Coca-Cola,120,,,,\n" + row + "\n")
    assert line == 3
    assert message in text


def test_duplicate_sku_names_both_lines() -> None:
    line, text = only(HEADER + "item,A,DUP,One,1,,,,\nitem,B,dup,Two,2,,,,\n")
    assert line == 3
    assert text == "duplicate SKU DUP (first used on line 2)"


@pytest.mark.parametrize(
    ("rows", "line", "message"),
    [
        (["option,,NOPE,Large,150,,Size,1,1"], 2, "no item with SKU NOPE above this line"),
        (["item,A,X1,Burger,100,,,,", "option,,X1,Large,150,,,1,1"], 3, "group is required"),
        (["item,A,X1,Burger,100,,,,", "option,,X1,Large,-1,,Size,1,1"], 3, "can't be negative"),
        (["item,A,X1,Burger,100,,,,", "option,,X1,Large,1,,Size,,"], 3, "needs both min and max"),
        (
            ["item,A,X1,Burger,100,,,,", "option,,X1,S,0,,Size,1,1", "option,,X1,L,1,,Size,0,1"],
            4,
            "min/max differ from group 'Size' on line 3",
        ),
        (["item,A,X1,Burger,100,,,,", "option,,X1,L,1,,Size,1,2"], 3, "max choices (2) is more"),
        (["item,A,X1,Burger,100,,,,", "option,,X1,L,1,,Size,2,1"], 3, "0 <= min <= max"),
        (
            ["item,A,X1,Burger,100,,,,", "option,,X1,L,1,,Size,1,2", "option,,X1,l,2,,Size,,"],
            4,
            "appears twice",
        ),
        (["item,A,X1,Burger,100,,,,", "option,A,X1,L,1,,Size,1,1"], 3, "leaves 'category'"),
    ],
)
def test_each_option_rule_reports_its_line(rows: list[str], line: int, message: str) -> None:
    found = problems(HEADER + "\n".join(rows) + "\n")
    assert any(at == line and message in text for at, text in found), found


def test_every_problem_is_reported_at_once() -> None:
    found = problems(
        HEADER
        + "item,A,X1,One,-1,,,,\n"
        + "item,A,X2,,100,,,,\n"
        + "item,A,X1,Again,100,,,,\n"
        + "item,A,X3,Fine,100,,,,\n"
        + "option,,X9,Large,1,,Size,1,1\n"
    )
    assert [line for line, _ in found] == [2, 3, 4, 6]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "the file is empty"),
        ("kind,category,sku,name\n", "missing column(s): price"),
        ("kind,category,sku,name,price,colour\n", "unknown column(s): colour"),
        ("kind,category,sku,name,price,price\n", "repeated column(s): price"),
    ],
)
def test_header_problems_are_on_line_1(text: str, message: str) -> None:
    assert only(text) == (1, message)


def test_non_utf8_files_are_refused() -> None:
    assert problems(HEADER.encode() + "item,A,X1,Café,1,,,,\n".encode("latin-1")) == [
        (None, "the file must be saved as UTF-8 text")
    ]


def test_blank_lines_are_skipped_and_counted() -> None:
    line, _ = only(HEADER + "\n" + "item,A,X1,One,-1,,,,\n")
    assert line == 3


def test_parse_rupees() -> None:
    assert parse_rupees("0") == 0
    assert parse_rupees("650") == 65000
    assert parse_rupees("650.5") == 65050
    assert parse_rupees("650.05") == 65005
    for bad in ("-1", "1,250", "1.234", " 1", "1e3", ""):
        with pytest.raises(ValueError, match=r"negative|rupees"):
            parse_rupees(bad)


def test_publishing_rules() -> None:
    empty = Menu(())
    assert validate_menu(empty) == []
    assert [p.message for p in validate_menu(empty, for_publish=True)] == [
        "a menu needs at least one item before it can be published"
    ]
    from velora.menu.model import Category

    lonely = Menu((Category("Deals", ()), Category("Drinks", (Item("COKE", "Coke", 12000),))))
    assert [p.path for p in validate_menu(lonely, for_publish=True)] == ["categories[0].items"]


def test_form_problems_carry_a_path() -> None:
    from velora.menu.model import Category

    menu = Menu((Category("Burgers", (Item("X1", "One", -5),)),))
    (problem,) = validate_menu(menu)
    assert (problem.line, problem.path) == (None, "categories[0].items[0].price_paisa")
    assert problem.message == "price can't be negative"
