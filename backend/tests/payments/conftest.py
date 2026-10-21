"""Fixtures for the P8 customer-side payment tests: the P7 order fixtures (a restaurant with
the test menu, transfer account details set, and a `Customer` driving a real chat)."""

from __future__ import annotations

import uuid
from typing import Any

from tests.conversation.conftest import TRANSFER_DETAILS, set_settings  # noqa: F401
from tests.db.conftest import PgDatabase
from tests.orders.conftest import (  # noqa: F401 - fixtures
    ADDRESS,
    BEEF_COD_TOTAL,
    BEEF_TRANSFER_TOTAL,
    Customer,
    Shop,
    chat,
    clock,
    cluster,
    db,
    fake,
    ids_of,
    orders_of,
    rows,
    run,
    shop,
    tenants,
    to_summary,
)


def payment_state(pg: PgDatabase, shop: Shop) -> dict[str, Any]:  # noqa: F811
    """Everything a payment could change: order statuses and versions, the ledger and the
    order history."""
    t = shop.tenant.value
    return {
        "orders": rows(
            pg,
            "SELECT id, status, version, updated_at FROM velora.orders WHERE tenant_id = %s "
            "ORDER BY id",
            t,
        ),
        "payments": rows(pg, "SELECT * FROM velora.payments WHERE tenant_id = %s", t),
        "transitions": rows(
            pg,
            "SELECT order_id, from_status, to_status FROM velora.order_transitions "
            "WHERE tenant_id = %s ORDER BY seq",
            t,
        ),
    }


def evidence_of(pg: PgDatabase, order_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return rows(
        pg,
        "SELECT kind, provider_media_id, mime_type FROM velora.payment_evidence "
        "WHERE order_id = %s ORDER BY received_at, id",
        order_id,
    )


def set_cod_block(pg: PgDatabase, customer: Customer, reason: str | None) -> None:
    _, customer_id = ids_of(pg, customer)
    with pg.admin() as conn:
        conn.execute(
            "UPDATE velora.customers SET cod_blocked_at = CASE WHEN %s::text IS NULL THEN NULL "
            "ELSE now() END, cod_block_reason = %s WHERE id = %s",
            (reason, reason, customer_id),
        )
