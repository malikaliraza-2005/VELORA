"""P9 backend: idempotency keys on every action (AD-20), the endpoints the dashboard added
(chats, channels, summary, late payments, confirmers), serving the app with its security
headers, and static checks on the dashboard's code."""

from __future__ import annotations

import asyncio
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Device, Restaurant, audit_rows, invite, join
from tests.db.conftest import PgDatabase
from tests.db.seed import insert_order
from velora.api import idempotency
from velora.app import create_app
from velora.clock import FakeClock
from velora.config import Settings
from velora.conversation.pktime import PKT

WEB = Path(__file__).resolve().parents[3] / "web"


def _transitions(db: PgDatabase, order: uuid.UUID, to: str) -> int:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT count(*) FROM velora.order_transitions WHERE order_id = %s AND to_status = %s",
            (order, to),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _keys(db: PgDatabase, restaurant: Restaurant) -> list[tuple[Any, ...]]:
    with db.admin() as conn:
        return conn.execute(
            "SELECT key, state, response_status FROM velora.idempotency_keys "
            "WHERE tenant_id = %s ORDER BY created_at",
            (restaurant.tenant.value,),
        ).fetchall()


# Idempotency keys (P9-T2, AD-20) ----------------------------------------------------------


def test_an_action_without_a_key_is_refused(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    with db.admin() as conn:
        order = insert_order(conn, restaurant.tenant.value)
    body = {"from": "cod_confirmed", "to": "accepted"}
    path = f"/api/orders/{order}/status"
    assert owner.request("POST", path, json=body, idempotency_key=None).status_code == 428
    assert owner.request("POST", path, json=body, idempotency_key="short").status_code == 400
    assert owner.request("POST", path, json=body, idempotency_key="x" * 101).status_code == 400
    assert _transitions(db, order, "accepted") == 0
    # Reads need no key.
    assert owner.request("GET", "/api/orders", idempotency_key=None).status_code == 200


def test_a_repeated_key_replays_the_first_answer_and_moves_once(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    with db.admin() as conn:
        order = insert_order(conn, restaurant.tenant.value)
    key = uuid.uuid4().hex
    body = {"from": "cod_confirmed", "to": "accepted"}
    first = owner.request("POST", f"/api/orders/{order}/status", json=body, idempotency_key=key)
    second = owner.request("POST", f"/api/orders/{order}/status", json=body, idempotency_key=key)
    assert first.status_code == 200 and first.json()["status"] == "accepted"
    assert "idempotent-replayed" not in first.headers
    assert second.status_code == 200
    assert second.headers["idempotent-replayed"] == "true"
    assert second.json() == {"replayed": True}
    assert _transitions(db, order, "accepted") == 1
    # Only the status is kept, never the body (which holds the customer's phone).
    assert _keys(db, restaurant)[-1] == (key, "done", 200)

    # 204 answers replay as 204.
    pin_key = uuid.uuid4().hex
    for _ in range(2):
        response = owner.request(
            "PUT", "/api/me/pin", json={"new_pin": "480135"}, idempotency_key=pin_key
        )
        assert response.status_code == 204
    assert response.headers["idempotent-replayed"] == "true"


def test_a_key_reused_for_another_request_is_refused(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    with db.admin() as conn:
        a = insert_order(conn, restaurant.tenant.value)
        b = insert_order(conn, restaurant.tenant.value)
    key = uuid.uuid4().hex
    body = {"from": "cod_confirmed", "to": "accepted"}
    assert (
        owner.request("POST", f"/api/orders/{a}/status", json=body, idempotency_key=key).status_code
        == 200
    )
    reused = owner.request("POST", f"/api/orders/{b}/status", json=body, idempotency_key=key)
    assert reused.status_code == 422
    assert _transitions(db, b, "accepted") == 0


def test_a_failed_action_frees_its_key(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    with db.admin() as conn:
        order = insert_order(conn, restaurant.tenant.value)
    key = uuid.uuid4().hex
    path = f"/api/orders/{order}/status"
    refused = owner.request(
        "POST", path, json={"from": "cod_confirmed", "to": "rejected"}, idempotency_key=key
    )
    assert refused.status_code == 422  # a reason is required
    assert [k for k in _keys(db, restaurant) if k[0] == key] == []
    # The same key with a corrected request runs.
    fixed = {"from": "cod_confirmed", "to": "rejected", "reason": "closed early"}
    assert owner.request("POST", path, json=fixed, idempotency_key=key).status_code == 200


def test_keys_belong_to_one_session(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    manager = join(client, owner, "manager")
    with db.admin() as conn:
        a = insert_order(conn, restaurant.tenant.value)
        b = insert_order(conn, restaurant.tenant.value)
    key = uuid.uuid4().hex
    body = {"from": "cod_confirmed", "to": "accepted"}
    assert (
        owner.request("POST", f"/api/orders/{a}/status", json=body, idempotency_key=key).status_code
        == 200
    )
    other = manager.request("POST", f"/api/orders/{b}/status", json=body, idempotency_key=key)
    assert other.status_code == 200 and "idempotent-replayed" not in other.headers


def test_a_key_still_in_progress_is_refused_until_abandoned(
    owner: Device, restaurant: Restaurant, db: PgDatabase, clock: FakeClock
) -> None:
    with db.admin() as conn:
        order = insert_order(conn, restaurant.tenant.value)
    key = uuid.uuid4().hex
    path = f"/api/orders/{order}/status"
    body = {"from": "cod_confirmed", "to": "accepted"}
    # A first attempt that never finished: claimed, then the process stopped.
    first = owner.request("POST", path, json=body, idempotency_key=key)
    assert first.status_code == 200
    with db.admin() as conn:
        conn.execute(
            "UPDATE velora.idempotency_keys SET state = 'in_progress', response_status = NULL, "
            "completed_at = NULL, created_at = %s WHERE key = %s",
            (clock.now(), key),
        )
    assert owner.request("POST", path, json=body, idempotency_key=key).status_code == 409
    clock.advance(idempotency.ABANDONED_AFTER + timedelta(seconds=1))
    # Taken over and run again: the move is refused by its own guard, not applied twice.
    retried = owner.request("POST", path, json=body, idempotency_key=key)
    assert retried.status_code == 409
    assert _transitions(db, order, "accepted") == 1


def test_old_keys_are_dropped(
    owner: Device, restaurant: Restaurant, db: PgDatabase, clock: FakeClock
) -> None:
    owner.request("PUT", "/api/me/pin", json={"new_pin": "480135"})
    assert len(_keys(db, restaurant)) >= 1
    clock.advance(idempotency.KEPT_FOR + timedelta(minutes=1))
    owner.request("PUT", "/api/me/pin", json={"current_pin": "480135", "new_pin": "592746"})
    assert len(_keys(db, restaurant)) == 1


# Chats (P9-T4) --------------------------------------------------------------------------------


def _chat(
    db: PgDatabase,
    restaurant: Restaurant,
    clock: FakeClock,
    *,
    state: str = "handoff",
    opted_out: bool = False,
) -> tuple[uuid.UUID, str]:
    phone = f"+92300{uuid.uuid4().int % 10**7:07d}"
    tenant = restaurant.tenant.value
    with db.admin() as conn, conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        row = conn.execute(
            "INSERT INTO velora.customers (tenant_id, phone_e164, opted_out_at) "
            "VALUES (%s, %s, CASE WHEN %s THEN now() END) RETURNING id",
            (tenant, phone, opted_out),
        ).fetchone()
        assert row is not None
        handoff = state == "handoff"
        found = conn.execute(
            "INSERT INTO velora.conversations (tenant_id, channel_id, customer_id, state, "
            "handoff_at, handoff_reason) SELECT %s, id, %s, %s, CASE WHEN %s THEN now() END, "
            "CASE WHEN %s THEN 'requested' END FROM velora.channels WHERE tenant_id = %s "
            "RETURNING id, channel_id",
            (tenant, row[0], state, handoff, handoff, tenant),
        ).fetchone()
        assert found is not None
        conn.execute(
            "INSERT INTO velora.inbox_events (tenant_id, channel_id, provider_event_id, "
            "event_type, ordering_key, payload, status, received_at) VALUES "
            "(%s, %s, %s, 'message', %s, %s, 'done', %s)",
            (
                tenant,
                found[1],
                uuid.uuid4().hex,
                phone,
                '{"kind": "text", "text": "hello?"}',
                clock.now() - timedelta(minutes=1),
            ),
        )
    return found[0], phone


def test_a_staff_reply_is_queued_audited_and_shown(
    owner: Device, restaurant: Restaurant, db: PgDatabase, clock: FakeClock
) -> None:
    conversation, phone = _chat(db, restaurant, clock)
    key = uuid.uuid4().hex
    path = f"/api/conversations/{conversation}/reply"
    sent = owner.request("POST", path, json={"text": "  On its way!  "}, idempotency_key=key)
    assert sent.status_code == 201
    message_id = sent.json()["message_id"]
    with db.admin() as conn:
        queued = conn.execute(
            "SELECT payload->>'to', payload->>'text', purpose, conversation_id "
            "FROM velora.outbox_messages WHERE id = %s",
            (message_id,),
        ).fetchone()
        audit = [
            r for r in audit_rows(conn, restaurant.tenant) if r[0] == "conversation.staff_replied"
        ]
    assert queued == (phone, "On its way!", "conversational", conversation)
    # The audit records the length, not the words.
    assert audit[0][4] == {"message_id": message_id, "length": 11}

    history = owner.request("GET", f"/api/conversations/{conversation}/messages").json()
    assert [(m["direction"], m["text"], m["status"]) for m in history] == [
        ("in", "hello?", None),
        ("out", "On its way!", "pending"),
    ]


@pytest.mark.parametrize(("state", "opted_out"), [("idle", False), ("handoff", True)])
def test_replies_only_go_to_handed_over_chats_of_customers_who_accept_messages(
    owner: Device,
    restaurant: Restaurant,
    db: PgDatabase,
    clock: FakeClock,
    state: str,
    opted_out: bool,
) -> None:
    conversation, _ = _chat(db, restaurant, clock, state=state, opted_out=opted_out)
    response = owner.request(
        "POST", f"/api/conversations/{conversation}/reply", json={"text": "Hi"}
    )
    assert response.status_code == 409
    with db.admin() as conn:
        assert conn.execute(
            "SELECT count(*) FROM velora.outbox_messages WHERE conversation_id = %s",
            (conversation,),
        ).fetchone() == (0,)


def test_kitchen_roles_cannot_read_or_answer_chats(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase, clock: FakeClock
) -> None:
    conversation, _ = _chat(db, restaurant, clock)
    kitchen = join(client, owner, "kitchen")
    assert kitchen.request("GET", f"/api/conversations/{conversation}/messages").status_code == 403
    assert (
        kitchen.request(
            "POST", f"/api/conversations/{conversation}/reply", json={"text": "x"}
        ).status_code
        == 403
    )
    assert kitchen.request("GET", "/api/conversations/message-problems").status_code == 403


def test_failed_and_unknown_messages_are_listed_for_staff(
    owner: Device, restaurant: Restaurant, db: PgDatabase, clock: FakeClock
) -> None:
    conversation, phone = _chat(db, restaurant, clock)
    tenant = restaurant.tenant.value
    with db.admin() as conn:
        for status in ("failed", "unknown", "sent"):
            conn.execute(
                "INSERT INTO velora.outbox_messages (tenant_id, channel_id, conversation_id, "
                "idempotency_key, kind, payload, ordering_key, status, created_at) "
                "SELECT %s, channel_id, id, %s, 'text', %s, %s, %s, %s "
                "FROM velora.conversations WHERE id = %s",
                (
                    tenant,
                    uuid.uuid4().hex,
                    f'{{"to": "{phone}", "text": "{status} one"}}',
                    phone,
                    status,
                    clock.now(),
                    conversation,
                ),
            )
    found = owner.request("GET", "/api/conversations/message-problems").json()
    assert sorted((m["status"], m["text"], m["to"]) for m in found) == [
        ("failed", "failed one", phone),
        ("unknown", "unknown one", phone),
    ]


# Payments: confirmers and late payments (P9-T3) ------------------------------------------------


def test_the_tablet_lists_people_who_can_confirm(
    client: TestClient, owner: Device, restaurant: Restaurant
) -> None:
    owner.request("PUT", "/api/me/pin", json={"new_pin": "480135"})
    invite(owner, "kitchen", "No PIN yet")
    tablet = join(client, owner, "kitchen_device")
    found = tablet.request("GET", "/api/payments/confirmers").json()
    assert found == [{"id": str(restaurant.owner_id), "display_name": "Owner"}]


def test_late_payments_lists_expired_transfer_orders_with_their_evidence(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    tenant = restaurant.tenant.value
    with db.admin() as conn:
        late = insert_order(conn, tenant, status="expired", payment_method="transfer")
        insert_order(conn, tenant, status="expired", payment_method="cod")
        insert_order(conn, tenant, status="awaiting_transfer", payment_method="transfer")
        conn.execute(
            "INSERT INTO velora.payment_evidence (tenant_id, order_id, kind, inbox_event_id, "
            "received_at) VALUES (%s, %s, 'text_claim', gen_random_uuid(), now())",
            (tenant, late),
        )
    found = owner.request("GET", "/api/orders/late-payments").json()
    assert [(f["order"]["id"], f["evidence_count"]) for f in found] == [(str(late), 1)]


# Today summary (P9-T5) --------------------------------------------------------------------------


def test_today_summary_counts_the_day(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    tenant = restaurant.tenant.value
    with db.admin() as conn:
        insert_order(conn, tenant, status="completed")
        insert_order(conn, tenant, status="completed", payment_method="transfer")
        insert_order(conn, tenant, status="cancelled")
        insert_order(conn, tenant)
    today = datetime.now(UTC).astimezone(PKT).date()
    found = owner.request("GET", f"/api/summary/today?day={today}").json()
    assert {k: found[k] for k in ("placed", "open", "completed", "cancelled", "expired")} == {
        "placed": 4,
        "open": 1,
        "completed": 2,
        "cancelled": 1,
        "expired": 0,
    }
    assert (
        found["completed_paisa"],
        found["completed_cod_paisa"],
        found["completed_transfer_paisa"],
    ) == (
        180000,
        90000,
        90000,
    )
    assert join(client, owner, "kitchen").request("GET", "/api/summary/today").status_code == 403


# WhatsApp channels (P9-T6) ------------------------------------------------------------------


def _channel(db: PgDatabase, restaurant: Restaurant, state: str) -> tuple[uuid.UUID, str]:
    with db.admin() as conn:
        row = conn.execute(
            "UPDATE velora.channels SET state = %s WHERE tenant_id = %s RETURNING id, session_ref",
            (state, restaurant.tenant.value),
        ).fetchone()
    assert row is not None
    return row[0], row[1]


def test_the_owner_relinks_and_gets_a_qr_image(
    owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    channel, _ = _channel(db, restaurant, "logged_out")
    listed = owner.request("GET", "/api/channels").json()
    assert [(c["state"], c["adapter_running"]) for c in listed] == [("logged_out", True)]
    assert owner.request("GET", f"/api/channels/{channel}/qr.svg").status_code == 204

    relinked = owner.request("POST", f"/api/channels/{channel}/relink")
    assert relinked.status_code == 200 and relinked.json()["state"] == "needs_qr"
    qr = owner.request("GET", f"/api/channels/{channel}/qr.svg")
    assert qr.status_code == 200
    assert qr.headers["content-type"].startswith("image/svg+xml")
    assert qr.headers["cache-control"] == "no-store"
    assert qr.text.startswith("<svg")
    with db.admin() as conn:
        events = conn.execute(
            "SELECT details->>'to', details->>'source' FROM velora.channel_events "
            "WHERE channel_id = %s AND event_type = 'state_changed'",
            (channel,),
        ).fetchall()
        audits = [
            r for r in audit_rows(conn, restaurant.tenant) if r[0] == "channel.relink_started"
        ]
    assert events == [("needs_qr", "relink")]
    assert audits[0][4] == {"from": "logged_out", "observed": "needs_qr"}
    # The QR code itself is not in the audit or event records.
    assert "fake-qr" not in repr(audits) + repr(events)


def test_a_connected_channel_or_one_without_an_adapter_is_not_relinked(
    owner: Device, restaurant: Restaurant, db: PgDatabase, settings: Settings, clock: FakeClock
) -> None:
    channel, _ = _channel(db, restaurant, "connected")
    assert owner.request("POST", f"/api/channels/{channel}/relink").status_code == 409
    _channel(db, restaurant, "needs_qr")
    # A deployment without the adapter (OpenWA before the Go) can't re-link from here.
    bare = create_app(settings, clock=clock)
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        # psycopg's async mode cannot use the Windows proactor event loop.
        options["loop_factory"] = asyncio.SelectorEventLoop
    with TestClient(bare, base_url=str(owner.client.base_url), backend_options=options) as other:
        device = Device(other)
        device.cookie, device.csrf = owner.cookie, owner.csrf
        assert device.request("GET", "/api/channels").json()[0]["adapter_running"] is False
        assert device.request("POST", f"/api/channels/{channel}/relink").status_code == 409


def test_only_the_owner_sees_channels(
    client: TestClient, owner: Device, restaurant: Restaurant, db: PgDatabase
) -> None:
    channel, _ = _channel(db, restaurant, "needs_qr")
    manager = join(client, owner, "manager")
    assert manager.request("GET", "/api/channels").status_code == 403
    assert manager.request("GET", f"/api/channels/{channel}/qr.svg").status_code == 403
    assert manager.request("POST", f"/api/channels/{channel}/relink").status_code == 403


# Serving the app (P9-T1) --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/login", "/sw.js", "/manifest.webmanifest", "/assets/js/main.js"]
)
def test_the_dashboard_is_served_with_its_security_headers(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    for directive in (
        "default-src 'none'",
        "script-src 'self'",
        "frame-ancestors 'none'",
        "require-trusted-types-for 'script'",
        "trusted-types velora",
    ):
        assert directive in csp
    assert "unsafe" not in csp
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_api_answers_are_never_cached(owner: Device) -> None:
    assert owner.request("GET", "/api/me").headers["cache-control"] == "no-store"


# Static checks on the dashboard's code ----------------------------------------------------------


def _scripts() -> list[Path]:
    return sorted([*WEB.rglob("*.js")])


def test_the_dashboard_never_writes_html_strings_or_evaluates_code() -> None:
    banned = re.compile(
        r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function"
        r"|localStorage|sessionStorage|indexedDB\.open"
    )
    found = {
        str(p.relative_to(WEB)): banned.findall(p.read_text(encoding="utf-8")) for p in _scripts()
    }
    assert {k: v for k, v in found.items() if v} == {}


def test_the_page_has_no_inline_script_or_style() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script(?![^>]*\bsrc=)", html) == []
    assert "<style" not in html and " style=" not in html
    assert re.findall(r"\son[a-z]+=", html) == []


def test_every_api_path_the_dashboard_calls_exists() -> None:
    """P9-T5: the dashboard uses the same endpoints (and so the same permissions) as the API."""
    routes = [
        re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
        for path in create_app(Settings(env="test", log_level="INFO")).openapi()["paths"]
    ]
    called: set[str] = set()
    for script in _scripts():
        for literal in re.findall(r"[`\"](/api/[^`\"?]*)", script.read_text(encoding="utf-8")):
            if literal != "/api/":  # the service worker's "never cache the API" prefix
                called.add(re.sub(r"\$\{[^}]+\}", "X", literal))
    assert len(called) > 25
    missing = sorted(p for p in called if not any(r.match(p) for r in routes))
    assert missing == []
