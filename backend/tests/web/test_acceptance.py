"""P9 exit criteria and task tests, in a real browser (simulated WhatsApp, AD-25):

- the kitchen-device role can't open settings (P9-T1);
- a double tap makes one transition (P9-T2), and new orders raise the alert;
- "Payment received" needs the reference and the PIN (P9-T3);
- a staff reply reaches the customer through the outbox, and Resume restores the bot (P9-T4);
- QR re-link restores the channel and clears the banner (P9-T6);
- with the network cut, actions are refused visibly and nothing is queued (P9-T7).

The staging run with a real test number (P9-T8) is gated by the OpenWA Go and P12.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from playwright.sync_api import BrowserContext, Page, expect

from tests.db.conftest import PgDatabase
from tests.db.seed import insert_order
from tests.web.conftest import (
    Server,
    Shop,
    api,
    invite_and_sign_in,
    run_async,
    sign_in,
    wait_until,
)
from velora.clock import SystemClock
from velora.dev.demo import SAMPLE_MENU_CSV
from velora.messaging.channels import ChannelMonitor
from velora.messaging.outbox import OutboxSender

PIN = "480135"


def _query(db: PgDatabase, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with db.admin() as conn:
        return conn.execute(sql, params).fetchall()


def _card(page: Page, order: uuid.UUID) -> Any:
    return page.locator(f"article.order[data-order='{order}']")


# P9-T1 --------------------------------------------------------------------------------------


def test_kitchen_device_cannot_open_settings(context: BrowserContext, shop: Shop) -> None:
    owner = sign_in(context, shop.owner_link)
    tablet_context = context.browser.new_context(base_url=owner.url.rstrip("/"))  # type: ignore[union-attr]
    try:
        tablet, _ = invite_and_sign_in(tablet_context, owner, "kitchen_device", "Kitchen tablet")
        views = tablet.eval_on_selector_all("nav.nav a", "els => els.map(e => e.dataset.view)")
        assert views == ["board", "payments", "me"]

        tablet.goto(tablet.url.split("#")[0] + "#/settings")
        expect(tablet.locator("#refused")).to_be_visible()
        expect(tablet.locator("#settings-form")).to_have_count(0)
        # The API refuses it too.
        me = tablet.request.get("/api/me").json()
        response = tablet.request.patch(
            "/api/settings/ordering",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": uuid.uuid4().hex},
            data={"ai_enabled": False},
        )
        assert response.status == 403
        assert tablet.request.get("/api/settings/ordering").status == 403
    finally:
        tablet_context.close()


# P9-T2 --------------------------------------------------------------------------------------


def test_new_order_alert_and_a_double_tap_makes_one_move(
    context: BrowserContext, shop: Shop, db: PgDatabase
) -> None:
    owner = sign_in(context, shop.owner_link)
    expect(owner.locator("[data-column=new] .empty")).to_be_visible()

    with db.admin() as conn:
        order = insert_order(conn, shop.tenant.value)
    card = _card(owner, order)
    expect(card).to_be_visible()
    expect(owner.locator("#new-order-banner")).to_contain_text("New order")

    card.get_by_role("button", name="Accept").dblclick()
    expect(_card(owner, order)).to_have_attribute("data-status", "accepted")
    time.sleep(1)
    moves = _query(
        db,
        "SELECT count(*) FROM velora.order_transitions WHERE order_id = %s "
        "AND to_status = 'accepted'",
        order,
    )
    assert moves == [(1,)]

    # The same request sent twice at once with one key (a retried tap): one move, and the
    # repeat is answered from the first.
    statuses = owner.evaluate(
        """async ([order, csrf]) => {
            const key = crypto.randomUUID();
            const send = () => fetch(`/api/orders/${order}/status`, {
                method: "POST",
                headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf,
                          "Idempotency-Key": key},
                body: JSON.stringify({from: "accepted", to: "preparing"}),
            });
            const first = await send();
            const second = await send();
            return [first.status, second.status, second.headers.get("Idempotent-Replayed")];
        }""",
        [str(order), owner.request.get("/api/me").json()["csrf_token"]],
    )
    assert statuses == [200, 200, "true"]
    moves = _query(
        db,
        "SELECT count(*) FROM velora.order_transitions WHERE order_id = %s "
        "AND to_status = 'preparing'",
        order,
    )
    assert moves == [(1,)]


# P9-T3 --------------------------------------------------------------------------------------


def test_payment_received_needs_the_reference_and_the_pin(
    context: BrowserContext, shop: Shop, db: PgDatabase
) -> None:
    owner = sign_in(context, shop.owner_link)
    api(owner, "PUT", "/api/me/pin", {"new_pin": PIN})
    with db.admin() as conn:
        order = insert_order(
            conn, shop.tenant.value, status="awaiting_transfer", payment_method="transfer"
        )
    _card(owner, order).get_by_role("button", name="Payment received").click()
    panel = owner.locator(".payment-panel")
    submit = panel.get_by_role("button", name="Confirm payment received")
    error = panel.locator(".form-error").first

    sent: list[str] = []
    owner.on("request", lambda r: sent.append(r.url) if "/payment/verify" in r.url else None)
    submit.click()
    expect(error).to_have_text("Enter the transaction reference.")
    panel.locator("input[name=reference]").fill("FT 2026 0001")
    submit.click()
    expect(error).to_have_text("Enter your payment PIN.")
    assert sent == []  # refused before anything was sent

    panel.locator("input[name=pin]").fill("111999")
    submit.click()
    expect(error).to_have_text("PIN not accepted")
    assert _query(db, "SELECT count(*) FROM velora.payments WHERE order_id = %s", order) == [(0,)]

    panel.locator("input[name=pin]").fill(PIN)
    submit.click()
    expect(owner.locator(".payment-panel .ok")).to_have_text("Payment confirmed.")
    expect(_card(owner, order)).to_have_attribute("data-status", "paid_staff_verified")
    assert _query(
        db, "SELECT status, reference FROM velora.payments WHERE order_id = %s", order
    ) == [("verified", "FT 2026 0001")]


def test_kitchen_tablet_names_the_confirmer(
    context: BrowserContext, shop: Shop, db: PgDatabase
) -> None:
    owner = sign_in(context, shop.owner_link)
    api(owner, "PUT", "/api/me/pin", {"new_pin": PIN})
    tablet_context = context.browser.new_context(base_url=owner.url.rstrip("/"))  # type: ignore[union-attr]
    try:
        tablet, _ = invite_and_sign_in(tablet_context, owner, "kitchen_device", "Tablet")
        with db.admin() as conn:
            order = insert_order(
                conn, shop.tenant.value, status="awaiting_transfer", payment_method="transfer"
            )
        _card(tablet, order).get_by_role("button", name="Payment received").click()
        panel = tablet.locator(".payment-panel")
        panel.locator("input[name=reference]").fill("FT20260002")
        panel.locator("input[name=pin]").fill(PIN)
        panel.get_by_role("button", name="Confirm payment received").click()
        expect(panel.locator(".form-error").first).to_have_text("Choose who is confirming.")
        panel.locator("select[name=confirmer_id]").select_option(label="Owner")
        panel.get_by_role("button", name="Confirm payment received").click()
        expect(tablet.locator(".payment-panel .ok")).to_have_text("Payment confirmed.")
        (row,) = _query(
            db,
            "SELECT verified_by_staff_id, recorded_by_staff_id <> verified_by_staff_id "
            "FROM velora.payments WHERE order_id = %s",
            order,
        )
        assert row == (shop.owner_id, True)
    finally:
        tablet_context.close()


# P9-T4 --------------------------------------------------------------------------------------


def _handed_over_chat(db: PgDatabase, shop: Shop, phone: str) -> uuid.UUID:
    with db.admin() as conn, conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(shop.tenant.value),))
        (customer,) = (
            conn.execute(
                "INSERT INTO velora.customers (tenant_id, phone_e164, display_name) "
                "VALUES (%s, %s, 'Ayesha') RETURNING id",
                (shop.tenant.value, phone),
            ).fetchone()
            or ()
        )
        (conversation,) = (
            conn.execute(
                "INSERT INTO velora.conversations (tenant_id, channel_id, customer_id, state, "
                "handoff_at, handoff_reason, context) VALUES (%s, %s, %s, 'handoff', now(), "
                '\'requested\', \'{"cart": [{"sku": "ZINGER", "quantity": 1, "choices": []}]}\') '
                "RETURNING id",
                (shop.tenant.value, shop.channel_id, customer),
            ).fetchone()
            or ()
        )
        conn.execute(
            "INSERT INTO velora.inbox_events (tenant_id, channel_id, provider_event_id, "
            "event_type, ordering_key, payload, status) VALUES (%s, %s, %s, 'message', %s, "
            "%s, 'done')",
            (
                shop.tenant.value,
                shop.channel_id,
                uuid.uuid4().hex,
                phone,
                '{"kind": "text", "text": "Is the zinger spicy?", "sender": "' + phone + '"}',
            ),
        )
    return conversation


def test_staff_reply_goes_through_the_outbox_and_resume_restores_the_bot(
    context: BrowserContext, shop: Shop, db: PgDatabase, server: Server
) -> None:
    phone = f"+92300{uuid.uuid4().int % 10**7:07d}"
    conversation = _handed_over_chat(db, shop, phone)
    owner = sign_in(context, shop.owner_link)
    expect(owner.locator("nav a[data-view=chats] .badge")).to_have_text("1")
    owner.click("nav a[data-view=chats]")
    owner.click(f"button[data-conversation='{conversation}']")
    expect(owner.locator(".msg-in")).to_contain_text("Is the zinger spicy?")

    owner.fill("textarea[name=text]", "Yes, a little. Want it mild?")
    owner.get_by_role("button", name="Send reply").click()
    expect(owner.locator(".msg-out")).to_contain_text("Yes, a little. Want it mild?")

    # The worker's outbox sender delivers it to the (simulated) customer.
    report = run_async(
        db,
        lambda d: OutboxSender(d, {"fake": server.provider}, SystemClock()).run_once(shop.tenant),
    )
    assert report.sent == 1
    assert server.provider.texts_to(phone) == ["Yes, a little. Want it mild?"]

    owner.get_by_role("button", name="Resume bot").click()
    expect(owner.locator("#chat .ok")).to_have_text("The assistant is handling this chat again.")
    assert _query(
        db, "SELECT state, context FROM velora.conversations WHERE id = %s", conversation
    ) == [("idle", {"cart": [{"sku": "ZINGER", "quantity": 1, "choices": []}]})]


# P9-T6 --------------------------------------------------------------------------------------


def test_qr_relink_restores_the_channel_and_clears_the_banner(
    context: BrowserContext, shop: Shop, db: PgDatabase, server: Server
) -> None:
    server.provider.set_state(shop.session_ref, "needs_qr")
    run_async(
        db, lambda d: ChannelMonitor(d, {"fake": server.provider}, SystemClock()).poll(shop.tenant)
    )
    owner = sign_in(context, shop.owner_link)
    expect(owner.locator("#whatsapp-banner")).to_contain_text("needs the QR code scanned")
    expect(owner.locator("#ordering-banner")).to_contain_text("WhatsApp is disconnected")

    owner.locator("#whatsapp-banner a").click()
    owner.get_by_role("button", name="Re-link WhatsApp").click()
    qr = owner.locator("#qr-image")
    expect(qr).to_be_visible()
    wait_until(owner, "document.getElementById('qr-image')?.naturalWidth > 0")
    response = owner.request.get(f"/api/channels/{shop.channel_id}/qr.svg")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("image/svg+xml")

    # The phone scans the code: the provider reports connected, and the health poll sees it.
    server.provider.set_state(shop.session_ref, "connected")
    run_async(
        db, lambda d: ChannelMonitor(d, {"fake": server.provider}, SystemClock()).poll(shop.tenant)
    )
    expect(owner.locator("#whatsapp-banner")).to_be_hidden()
    # Orders are no longer stopped for WhatsApp (this test restaurant has no menu, so that is
    # what the banner says next).
    expect(owner.locator("#ordering-banner")).not_to_contain_text("WhatsApp")
    expect(owner.locator(".channel-connected")).to_contain_text("Connected")
    expect(owner.locator("#qr-image")).to_have_count(0)


def test_the_qr_is_for_the_owner_only(context: BrowserContext, shop: Shop) -> None:
    owner = sign_in(context, shop.owner_link)
    manager_context = context.browser.new_context(base_url=owner.url.rstrip("/"))  # type: ignore[union-attr]
    try:
        manager, _ = invite_and_sign_in(manager_context, owner, "manager", "Manager")
        assert manager.request.get(f"/api/channels/{shop.channel_id}/qr.svg").status == 403
        assert manager.request.get("/api/channels").status == 403
        expect(manager.locator("nav a[data-view=whatsapp]")).to_have_count(0)
    finally:
        manager_context.close()


# P9-T7 --------------------------------------------------------------------------------------


def test_offline_actions_are_refused_visibly_and_nothing_is_queued(
    context: BrowserContext, shop: Shop, db: PgDatabase
) -> None:
    with db.admin() as conn:
        order = insert_order(conn, shop.tenant.value)
    owner = sign_in(context, shop.owner_link)
    accept = _card(owner, order).get_by_role("button", name="Accept")
    expect(accept).to_be_visible()

    context.set_offline(True)
    expect(owner.locator("#connection-banner")).to_be_visible()
    accept.click()
    expect(owner.locator(".toast-error")).to_contain_text("Connection lost — this was not sent")

    context.set_offline(False)
    expect(owner.locator("#connection-banner")).to_be_hidden()
    # Give any queued request every chance to go out: two polls.
    owner.wait_for_timeout(11_000)
    assert _query(db, "SELECT status FROM velora.orders WHERE id = %s", order) == [
        ("cod_confirmed",)
    ]
    assert _query(
        db, "SELECT count(*) FROM velora.order_transitions WHERE order_id = %s", order
    ) == [(0,)]


# P9-T5: menu ----------------------------------------------------------------------------------


def test_the_owner_reviews_and_publishes_an_imported_menu(
    context: BrowserContext, shop: Shop
) -> None:
    owner = sign_in(context, shop.owner_link)
    owner.click("nav a[data-view=menu]")
    expect(owner.locator("#menu-draft")).to_contain_text("No draft")
    owner.fill("textarea[name=csv]", SAMPLE_MENU_CSV)
    owner.get_by_role("button", name="Import into draft").click()
    expect(owner.locator("#menu-draft")).to_contain_text("Draft version 1")
    expect(owner.locator("#menu-draft")).to_contain_text("Added: BIRYANI")

    owner.once("dialog", lambda d: d.accept())
    owner.get_by_role("button", name="Publish this draft").click()
    expect(owner.locator(".view-menu")).to_contain_text("Live menu, version 1")
    biryani = owner.locator("li[data-sku=BIRYANI]")
    biryani.get_by_role("button", name="Mark sold out").click()
    expect(biryani).to_contain_text("Sold out")
    # A 204 answer refreshes the screen without an error.
    expect(owner.locator(".toast-error")).to_have_count(0)


def test_a_bad_csv_lists_every_problem(context: BrowserContext, shop: Shop) -> None:
    owner = sign_in(context, shop.owner_link)
    owner.click("nav a[data-view=menu]")
    owner.fill(
        "textarea[name=csv]",
        "kind,category,sku,name,price,description,group,min,max\n"
        "item,Burgers,ZINGER,Zinger,abc,,,,\nitem,Burgers,ZINGER,Again,100,,,,\n",
    )
    owner.get_by_role("button", name="Import into draft").click()
    error = owner.locator("#menu-draft .form-error")
    expect(error).to_contain_text("Line 2")
    expect(error).to_contain_text("Line 3")


def test_setting_the_pin_on_the_me_screen(context: BrowserContext, shop: Shop) -> None:
    owner = sign_in(context, shop.owner_link)
    owner.click("nav a[data-view=me]")
    fields = owner.locator(".view-me input[type=password]")
    fields.nth(1).fill("480135")
    fields.nth(2).fill("480136")
    owner.get_by_role("button", name="Save PIN").click()
    expect(owner.locator(".view-me .form-error")).to_have_text("The two new PINs don't match.")
    fields.nth(2).fill("480135")
    owner.get_by_role("button", name="Save PIN").click()
    expect(owner.locator(".view-me .ok")).to_have_text("PIN saved.")
    expect(owner.locator(".toast-error")).to_have_count(0)
