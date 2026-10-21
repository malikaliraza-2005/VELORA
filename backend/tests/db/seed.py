"""Seed one row in every tenant table for a tenant. Test numbers only (+92300000xxxx)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


def _one(conn: psycopg.Connection[Any], query: bytes | str, params: tuple[Any, ...]) -> UUID:
    row = conn.execute(query.encode() if isinstance(query, str) else query, params).fetchone()
    assert row is not None
    value = row[0]
    assert isinstance(value, UUID)
    return value


def seed_tenant(conn: psycopg.Connection[Any], slug: str, prefix: str) -> dict[str, UUID]:
    """Insert a complete tenant graph. `conn` must be able to write every table (admin).

    Returns table name -> id of the row seeded in that table.
    """
    ids: dict[str, UUID] = {}
    now = datetime.now(UTC)
    with conn.transaction():
        ids["tenants"] = t = _one(
            conn,
            "INSERT INTO velora.tenants (slug, display_name, order_number_prefix) "
            "VALUES (%s, %s, %s) RETURNING id",
            (slug, f"Test {slug}", prefix),
        )
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t),))
        ids["channels"] = ch = _one(
            conn,
            "INSERT INTO velora.channels (tenant_id, provider, session_ref, state) "
            "VALUES (%s, 'fake', %s, 'connected') RETURNING id",
            (t, f"session-{slug}"),
        )
        ids["staff_members"] = staff = _one(
            conn,
            "INSERT INTO velora.staff_members (tenant_id, display_name, role) "
            "VALUES (%s, 'Owner', 'owner') RETURNING id",
            (t,),
        )
        ids["staff_sessions"] = _one(
            conn,
            "INSERT INTO velora.staff_sessions "
            "(tenant_id, staff_member_id, token_hash, absolute_expires_at) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (t, staff, secrets.token_bytes(32), now + timedelta(days=30)),
        )
        ids["magic_links"] = _one(
            conn,
            "INSERT INTO velora.magic_links (tenant_id, staff_member_id, token_hash, purpose, "
            "issued_by_kind, created_at, expires_at) "
            "VALUES (%s, %s, %s, 'invite', 'platform', %s, %s) RETURNING id",
            (t, staff, secrets.token_bytes(32), now, now + timedelta(minutes=15)),
        )
        ids["consents"] = _one(
            conn,
            "INSERT INTO velora.consents (tenant_id, kind, document_version, document_sha256, "
            "accepted_by_name, accepted_by_staff_id) "
            "VALUES (%s, 'separate_number_ban_risk', 'v1', %s, 'Owner', %s) RETURNING id",
            (t, "0" * 64, staff),
        )
        ids["owner_verifications"] = _one(
            conn,
            "INSERT INTO velora.owner_verifications (tenant_id, method) "
            "VALUES (%s, 'in_person') RETURNING id",
            (t,),
        )
        ids["customers"] = cust = _one(
            conn,
            "INSERT INTO velora.customers (tenant_id, phone_e164, display_name) "
            "VALUES (%s, '+923000000001', 'Test Customer') RETURNING id",
            (t,),
        )
        ids["conversations"] = conv = _one(
            conn,
            "INSERT INTO velora.conversations (tenant_id, channel_id, customer_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (t, ch, cust),
        )
        ids["inbox_events"] = _one(
            conn,
            "INSERT INTO velora.inbox_events "
            "(tenant_id, channel_id, provider_event_id, event_type, payload) "
            "VALUES (%s, %s, 'evt-1', 'message', %s) RETURNING id",
            (t, ch, Jsonb({"text": "menu"})),
        )
        ids["outbox_messages"] = _one(
            conn,
            "INSERT INTO velora.outbox_messages "
            "(tenant_id, channel_id, conversation_id, idempotency_key, kind, payload) "
            "VALUES (%s, %s, %s, 'reply-1', 'text', %s) RETURNING id",
            (t, ch, conv, Jsonb({"text": "Here is the menu"})),
        )
        ids["pending_actions"] = action = _one(
            conn,
            "INSERT INTO velora.pending_actions "
            "(tenant_id, conversation_id, customer_id, kind, nonce_hash, cart_hash, payload, "
            "expires_at) VALUES (%s, %s, %s, 'confirm_order', %s, %s, %s, %s) RETURNING id",
            (
                t,
                conv,
                cust,
                secrets.token_bytes(32),
                secrets.token_bytes(32),
                Jsonb({}),
                now + timedelta(minutes=15),
            ),
        )
        ids["orders"] = order = _one(
            conn,
            "INSERT INTO velora.orders (tenant_id, order_number, customer_id, conversation_id, "
            "pending_action_id, status, fulfilment, payment_method, subtotal_paisa, "
            "delivery_fee_paisa, total_paisa, delivery_address) "
            "VALUES (%s, velora.next_order_number(), %s, %s, %s, 'cod_confirmed', 'delivery', "
            "'cod', 90000, 15000, 105000, 'House 1, Street 2') RETURNING id",
            (t, cust, conv, action),
        )
        ids["order_items"] = _one(
            conn,
            "INSERT INTO velora.order_items (tenant_id, order_id, line_no, sku, name_snapshot, "
            "unit_price_paisa, quantity, line_total_paisa) "
            "VALUES (%s, %s, 1, 'BURGER-1', 'Zinger Burger', 45000, 2, 90000) RETURNING id",
            (t, order),
        )
        ids["order_transitions"] = _one(
            conn,
            "INSERT INTO velora.order_transitions "
            "(tenant_id, order_id, from_status, to_status, actor_kind) "
            "VALUES (%s, %s, 'awaiting_confirmation', 'cod_confirmed', 'customer') RETURNING id",
            (t, order),
        )
        ids["payments"] = _one(
            conn,
            "INSERT INTO velora.payments (tenant_id, order_id, method, amount_paisa, status, "
            "verified_by_staff_id, verified_at) VALUES (%s, %s, 'cod', 105000, 'verified', %s, "
            "now()) RETURNING id",
            (t, order, staff),
        )
        ids["payment_evidence"] = _one(
            conn,
            "INSERT INTO velora.payment_evidence (tenant_id, order_id, kind, inbox_event_id, "
            "received_at) VALUES (%s, %s, 'text_claim', gen_random_uuid(), now()) RETURNING id",
            (t, order),
        )
        ids["audit_log"] = _one(
            conn,
            "INSERT INTO velora.audit_log (tenant_id, actor_kind, action, target_type, target_id) "
            "VALUES (%s, 'system', 'order.created', 'order', %s) RETURNING id",
            (t, order),
        )
        # P4: one published menu version with one row in each catalogue table.
        ids["menu_versions"] = version = _one(
            conn,
            "INSERT INTO velora.menu_versions (tenant_id, version_no, created_by_kind, "
            "created_by_staff_id) VALUES (%s, 1, 'staff', %s) RETURNING id",
            (t, staff),
        )
        ids["menu_categories"] = category = _one(
            conn,
            "INSERT INTO velora.menu_categories (tenant_id, menu_version_id, position, name) "
            "VALUES (%s, %s, 0, 'Burgers') RETURNING id",
            (t, version),
        )
        ids["menu_items"] = item = _one(
            conn,
            "INSERT INTO velora.menu_items (tenant_id, menu_version_id, category_id, position, "
            "sku, name, price_paisa) VALUES (%s, %s, %s, 0, 'BURGER-1', 'Zinger Burger', 45000) "
            "RETURNING id",
            (t, version, category),
        )
        ids["modifier_groups"] = group = _one(
            conn,
            "INSERT INTO velora.modifier_groups (tenant_id, menu_version_id, item_id, position, "
            "name, min_select, max_select) VALUES (%s, %s, %s, 0, 'Size', 1, 1) RETURNING id",
            (t, version, item),
        )
        ids["modifier_options"] = _one(
            conn,
            "INSERT INTO velora.modifier_options (tenant_id, menu_version_id, group_id, position, "
            "name, price_delta_paisa) VALUES (%s, %s, %s, 0, 'Regular', 0) RETURNING id",
            (t, version, group),
        )
        conn.execute("UPDATE velora.menu_versions SET frozen_at = now() WHERE id = %s", (version,))
        ids["menu_publications"] = _one(
            conn,
            "INSERT INTO velora.menu_publications (tenant_id, publication_no, menu_version_id, "
            "published_by_staff_id) VALUES (%s, 1, %s, %s) RETURNING id",
            (t, version, staff),
        )
        ids["menu_item_availability"] = _one(
            conn,
            "INSERT INTO velora.menu_item_availability (tenant_id, sku, is_available, "
            "updated_by_staff_id) VALUES (%s, 'BURGER-1', true, %s) RETURNING id",
            (t, staff),
        )
        ids["ordering_settings"] = _one(
            conn,
            "INSERT INTO velora.ordering_settings (tenant_id) VALUES (%s) RETURNING id",
            (t,),
        )
        ids["order_capacity_slots"] = _one(
            conn,
            "INSERT INTO velora.order_capacity_slots (tenant_id, slot_start, orders_count) "
            "VALUES (%s, '2026-10-21 09:00:00+00', 1) RETURNING id",
            (t,),
        )
        ids["channel_events"] = _one(
            conn,
            "INSERT INTO velora.channel_events (tenant_id, channel_id, event_type) "
            "VALUES (%s, %s, 'connected') RETURNING id",
            (t, ch),
        )
    return ids


def insert_order(
    conn: psycopg.Connection[Any],
    tenant_id: UUID,
    *,
    status: str = "cod_confirmed",
    payment_method: str = "cod",
    fulfilment: str = "delivery",
    customer_id: UUID | None = None,
    conversation_id: UUID | None = None,
    payment_due_at: datetime | None = None,
) -> UUID:
    """An order in any status, written as the administrator (P7 tests). Creates a customer if
    none is given. A transfer order awaiting payment gets a due time an hour from now."""
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        if customer_id is None:
            customer_id = _one(
                conn,
                "INSERT INTO velora.customers (tenant_id, phone_e164) VALUES (%s, %s) RETURNING id",
                (tenant_id, f"+92301{secrets.randbelow(10**7):07d}"),
            )
        if status == "awaiting_transfer" and payment_due_at is None:
            payment_due_at = datetime.now(UTC) + timedelta(hours=1)
        return _one(
            conn,
            "INSERT INTO velora.orders (tenant_id, order_number, customer_id, conversation_id, "
            "status, fulfilment, payment_method, subtotal_paisa, total_paisa, payment_due_at) "
            "VALUES (%s, velora.next_order_number(), %s, %s, %s, %s, %s, 90000, 90000, %s) "
            "RETURNING id",
            (
                tenant_id,
                customer_id,
                conversation_id,
                status,
                fulfilment,
                payment_method,
                payment_due_at,
            ),
        )
