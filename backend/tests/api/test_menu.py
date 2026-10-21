"""P4-T1 to P4-T4: catalogue, import, draft/review/publish with history, availability.

Exit criteria covered here: an invalid CSV is rejected with line-level errors; an unavailable
item is never offered. Also: draft items are invisible to customer-facing queries, and
re-publishing the previous version restores it; price changes are audited.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import (
    SAMPLE_CSV,
    Device,
    Restaurant,
    audit_rows,
    join,
    publish,
    publish_menu,
    run_async,
    sample_menu,
    save_draft,
    services_of,
)
from tests.db.conftest import PLATFORM_ROLE, PgDatabase
from velora.admin.menu import import_menu
from velora.menu.csv_import import CsvImportError
from velora.menu.store import LiveMenu, live_menu
from velora.ordering.pricing import CartLine, QuoteRejected


def customer_menu(client: TestClient, restaurant: Restaurant) -> LiveMenu | None:
    """The customer-facing read, under the tenant the channel resolver would give."""

    async def read() -> LiveMenu | None:
        async with services_of(client).menu._db.tenant(restaurant.tenant) as conn:
            return await live_menu(conn)

    return run_async(client, read)


def offered_skus(client: TestClient, restaurant: Restaurant) -> set[str]:
    found = customer_menu(client, restaurant)
    return set() if found is None else {i.sku for i in found.offerable().items()}


def quote_zinger(client: TestClient, restaurant: Restaurant, sku: str = "ZINGER") -> int:
    choices = (("Size", "Regular"),) if sku == "ZINGER" else ()
    quote = run_async(
        client,
        services_of(client).ordering.quote,
        restaurant.tenant,
        [CartLine(sku, 1, choices)],
        fulfilment="takeaway",
        payment_method="cod",
    )
    return quote.subtotal_paisa


@pytest.fixture()
def taxed(owner: Device) -> Device:
    """Placeholder tax values (B3: the real ones wait for the accountant)."""
    response = owner.request(
        "PATCH", "/api/settings/ordering", json={"tax_cod_bps": 0, "tax_transfer_bps": 0}
    )
    assert response.status_code == 200, response.text
    return owner


# Draft, review and publish (T3) -----------------------------------------------------------


def test_draft_items_are_invisible_until_published(
    client: TestClient, taxed: Device, restaurant: Restaurant
) -> None:
    owner = taxed
    assert customer_menu(client, restaurant) is None
    draft = save_draft(owner)
    assert draft["status"] == "draft"
    # Saved but not published: customers see nothing at all.
    assert customer_menu(client, restaurant) is None

    publish_menu(owner)
    assert offered_skus(client, restaurant) == {"ZINGER", "SMASH", "COKE"}

    # A new draft with an extra item and a price rise is still invisible.
    save_draft(owner, sample_menu(70000, extra_sku="FRIES"))
    assert offered_skus(client, restaurant) == {"ZINGER", "SMASH", "COKE"}
    assert quote_zinger(client, restaurant) == 65000
    with pytest.raises(QuoteRejected) as rejected:
        quote_zinger(client, restaurant, "FRIES")
    assert rejected.value.code == "unknown_item"


def test_republishing_the_previous_version_restores_it(
    client: TestClient, taxed: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    owner = taxed
    v1 = publish_menu(owner)
    v2 = publish_menu(owner, sample_menu(70000, extra_sku="FRIES"))
    assert v2["version_no"] == v1["version_no"] + 1
    assert quote_zinger(client, restaurant) == 70000
    assert "FRIES" in offered_skus(client, restaurant)

    assert publish(owner, v1).status_code == 200
    assert quote_zinger(client, restaurant) == 65000
    assert "FRIES" not in offered_skus(client, restaurant)

    history = owner.request("GET", "/api/menu/versions").json()
    states = {v["version_no"]: v["status"] for v in history["versions"]}
    assert states == {v1["version_no"]: "live", v2["version_no"]: "previous"}
    assert [p["version_no"] for p in history["publications"]] == [
        v1["version_no"],
        v2["version_no"],
        v1["version_no"],
    ]
    # The rollback is audited with the price changes it made.
    published = _audit(db, restaurant, "menu.published")
    (rollback,) = [d for d in published if d["republished"]]
    assert rollback["version_no"] == v1["version_no"]
    assert {"key": "ZINGER", "from_paisa": 70000, "to_paisa": 65000} in rollback["price_changes"]
    assert rollback["removed_skus"] == ["FRIES"]


def test_publish_needs_the_reviewed_revision(owner: Device) -> None:
    reviewed = save_draft(owner)
    newer = save_draft(owner, sample_menu(99000))
    assert newer["id"] == reviewed["id"] and newer["revision"] == reviewed["revision"] + 1
    # Publishing what was reviewed earlier is refused: the draft changed since.
    assert publish(owner, reviewed).status_code == 409
    assert publish(owner, newer).status_code == 200
    # The live version is frozen; publishing it again changes nothing.
    assert publish(owner, newer).status_code == 409


def test_saving_over_someone_elses_changes_is_refused(owner: Device) -> None:
    save_draft(owner)
    stale = {"base_revision": 0, "menu": sample_menu()}
    response = owner.request("PUT", "/api/menu/draft", json=stale)
    assert response.status_code == 409


def test_an_empty_menu_cannot_be_published(owner: Device) -> None:
    empty = save_draft(owner, {"categories": [{"name": "Burgers", "items": []}]})
    response = publish(owner, empty)
    assert response.status_code == 422
    messages = [p["message"] for p in response.json()["problems"]]
    assert any("at least one item" in m for m in messages)


def test_draft_view_shows_changes_against_the_live_menu(owner: Device) -> None:
    publish_menu(owner)
    save_draft(owner, sample_menu(70000, extra_sku="FRIES"))
    changes = owner.request("GET", "/api/menu/draft").json()["changes_vs_live"]
    assert changes["added_skus"] == ["FRIES"]
    assert changes["price_changes"] == [{"key": "ZINGER", "from_paisa": 65000, "to_paisa": 70000}]


def test_published_versions_can_be_reviewed(owner: Device) -> None:
    v1 = publish_menu(owner)
    publish_menu(owner, sample_menu(70000))
    menu = owner.request("GET", f"/api/menu/versions/{v1['id']}").json()
    assert [c["name"] for c in menu["categories"]] == ["Burgers", "Drinks"]
    zinger = menu["categories"][0]["items"][0]
    assert (zinger["sku"], zinger["price_paisa"]) == ("ZINGER", 65000)
    assert [o["name"] for o in zinger["groups"][0]["options"]] == ["Regular", "Large"]


# Price audit (T1) -------------------------------------------------------------------------


def _audit(db: PgDatabase, restaurant: Restaurant, action: str) -> list[dict[str, Any]]:
    """The details of each `action` audit row, oldest first."""
    with db.admin() as conn:
        return [r[4] for r in audit_rows(conn, restaurant.tenant) if r[0] == action]


def test_price_changes_are_audited(owner: Device, restaurant: Restaurant, db: PgDatabase) -> None:
    publish_menu(owner)
    save_draft(owner, sample_menu(72000))
    with db.admin() as conn:
        saved = [r for r in audit_rows(conn, restaurant.tenant) if r[0] == "menu.draft_saved"]
    (change,) = [r for r in saved if r[4]["price_changes"]]
    assert change[1:3] == ("staff", owner.staff_id)
    assert change[4]["price_changes"] == [{"key": "ZINGER", "from_paisa": 65000, "to_paisa": 72000}]
    published = _audit(db, restaurant, "menu.published")
    assert all(d["price_changes"] == [] for d in published)  # first publish: nothing to compare


# CSV import (T2) --------------------------------------------------------------------------


def test_csv_import_fills_the_draft(owner: Device) -> None:
    response = owner.request(
        "POST", "/api/menu/draft/import", json={"base_revision": 0, "csv": SAMPLE_CSV}
    )
    assert response.status_code == 200, response.text
    draft = owner.request("GET", "/api/menu/draft").json()
    assert draft["menu"]["categories"][0]["items"][0]["groups"][0]["options"][1] == {
        "name": "Large",
        "price_delta_paisa": 15000,
    }


def test_invalid_csv_is_rejected_with_line_level_errors(owner: Device) -> None:
    bad = (
        "kind,category,sku,name,price\n"
        "item,Burgers,ZINGER,Zinger Burger,650\n"
        "item,Burgers,ZINGER,Zinger Again,700\n"
        "item,Burgers,FREE,Free Burger,-5\n"
        "item,Burgers,NONAME,,300\n"
    )
    response = owner.request(
        "POST", "/api/menu/draft/import", json={"base_revision": 0, "csv": bad}
    )
    assert response.status_code == 422
    problems = response.json()["problems"]
    assert {p["line"] for p in problems} == {3, 4, 5}
    by_line = {p["line"]: p["message"] for p in problems}
    assert "duplicate SKU ZINGER" in by_line[3]
    assert "negative" in by_line[4]
    assert "item name is required" in by_line[5]
    # Nothing was saved.
    assert owner.request("GET", "/api/menu/draft").json()["base_revision"] == 0


def test_founder_import_fills_a_draft_the_owner_publishes(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    url = db.cluster.url(db.name, PLATFORM_ROLE)
    result = _run(import_menu(url, restaurant.tenant, SAMPLE_CSV.encode(), operator="founder"))
    assert (result.version_no, result.items) == (1, 3)
    with db.admin() as conn:
        row = next(r for r in audit_rows(conn, restaurant.tenant) if r[0] == "menu.draft_imported")
    assert row[1] == "platform" and row[4]["operator"] == "founder"
    # Not live until the owner reviews and publishes it.
    assert customer_menu(client, restaurant) is None
    draft = owner.request("GET", "/api/menu/draft").json()
    response = owner.request(
        "POST",
        f"/api/menu/versions/{draft['version_id']}/publish",
        json={"revision": draft["base_revision"]},
    )
    assert response.status_code == 200
    assert offered_skus(client, restaurant) == {"ZINGER", "SMASH", "COKE"}


def test_founder_import_rejects_an_invalid_file(restaurant: Restaurant, db: PgDatabase) -> None:
    url = db.cluster.url(db.name, PLATFORM_ROLE)
    with pytest.raises(CsvImportError) as failed:
        _run(
            import_menu(
                url, restaurant.tenant, b"kind,category,sku,name,price\nx,,,,\n", operator="f"
            )
        )
    assert failed.value.problems[0].line == 2


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro, loop_factory=loop)


# Availability (T4) ------------------------------------------------------------------------


def test_an_unavailable_item_is_never_offered(
    client: TestClient, taxed: Device, restaurant: Restaurant
) -> None:
    owner = taxed
    publish_menu(owner)
    response = owner.request("PUT", "/api/menu/availability/zinger", json={"available": False})
    assert response.status_code == 204

    assert "ZINGER" not in offered_skus(client, restaurant)
    with pytest.raises(QuoteRejected) as rejected:
        quote_zinger(client, restaurant)
    assert rejected.value.code == "unavailable"
    live = owner.request("GET", "/api/menu/live").json()
    zinger = next(i for c in live["categories"] for i in c["items"] if i["sku"] == "ZINGER")
    assert zinger["available"] is False

    # Availability survives a re-publish: the SKU stays unavailable in the next version.
    publish_menu(owner, sample_menu(70000))
    assert "ZINGER" not in offered_skus(client, restaurant)

    owner.request("PUT", "/api/menu/availability/ZINGER", json={"available": True})
    assert "ZINGER" in offered_skus(client, restaurant)
    assert quote_zinger(client, restaurant) == 70000


def test_availability_of_an_unknown_item_is_refused(owner: Device) -> None:
    publish_menu(owner)
    response = owner.request("PUT", "/api/menu/availability/NOPE", json={"available": False})
    assert response.status_code == 404


# Permissions ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["kitchen", "kitchen_device"])
def test_kitchen_roles_cannot_change_the_menu(client: TestClient, owner: Device, role: str) -> None:
    version = save_draft(owner)
    device = join(client, owner, role)
    assert device.request("GET", "/api/menu/draft").status_code == 403
    body = {"base_revision": version["revision"], "menu": sample_menu()}
    assert device.request("PUT", "/api/menu/draft", json=body).status_code == 403
    assert publish(device, version).status_code == 403
    response = device.request("PUT", "/api/menu/availability/ZINGER", json={"available": False})
    assert response.status_code == 403


def test_a_manager_can_edit_and_publish(client: TestClient, owner: Device) -> None:
    manager = join(client, owner, "manager")
    publish_menu(manager)


def test_menus_are_isolated_between_restaurants(
    client: TestClient, owner: Device, platform: Any, settings: Any
) -> None:
    from tests.api.conftest import new_restaurant

    version = publish_menu(owner)
    other = new_restaurant(platform, settings)
    other_owner = Device(client)
    assert other_owner.login(other.owner_link).status_code == 200
    assert other_owner.request("GET", f"/api/menu/versions/{version['id']}").status_code == 404
    assert publish(other_owner, version).status_code == 404
    assert other_owner.request("GET", "/api/menu/live").status_code == 404


def test_menu_import_command(
    restaurant: Restaurant,
    platform: Any,
    db: PgDatabase,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from velora.admin.__main__ import main

    slug = next(t.slug for t in platform.list_tenants() if t.id == restaurant.tenant.value)
    monkeypatch.setenv("VELORA_PLATFORM_DATABASE_URL", db.cluster.url(db.name, PLATFORM_ROLE))
    bad = tmp_path / "bad.csv"
    bad.write_text("kind,category,sku,name,price\nitem,A,X1,One,-1\n", encoding="utf-8")
    good = tmp_path / "menu.csv"
    good.write_text(SAMPLE_CSV, encoding="utf-8")

    args = ["--operator", "founder", "menu-import", "--tenant", slug, "--file"]
    assert main([*args, str(bad)]) == 1
    assert "line 2: price can't be negative" in capsys.readouterr().err
    assert main([*args, str(good)]) == 0
    assert "3 items. The owner must review and publish it" in capsys.readouterr().out
