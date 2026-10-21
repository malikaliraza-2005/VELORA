"""P7-T3: order states and the transition table.

Exit criterion: **illegal transitions are rejected**, by the engine and by the database guard.
Also: a customer can cancel only before the order is accepted, and every transition writes a
history row and an audit row.
"""

from __future__ import annotations

import itertools
import re
import uuid
from datetime import timedelta
from typing import Any

import pytest
from psycopg import errors

from tests.db.conftest import PgDatabase
from tests.db.seed import insert_order
from tests.messaging.conftest import Env
from tests.orders.conftest import (
    Shop,
    chat,
    orders_of,
    rows,
    slot_count,
    staff_id_of,
    to_summary,
)
from velora.conversation import copy
from velora.orders import lifecycle
from velora.orders.lifecycle import (
    STATUSES,
    TRANSITIONS,
    Actor,
    IllegalTransitionError,
    InvalidReasonError,
    StaleTransitionError,
    find_rule,
    transition,
)

KINDS = [
    ("cod", "delivery"),
    ("cod", "takeaway"),
    ("transfer", "delivery"),
    ("transfer", "takeaway"),
]


def test_the_table_is_consistent() -> None:
    pairs = [(r.from_status, r.to_status, r.payment_method, r.fulfilment) for r in TRANSITIONS]
    assert len(pairs) == len(set(pairs))
    for rule in TRANSITIONS:
        assert rule.actors and rule.from_status != rule.to_status
        assert rule.from_status not in lifecycle.TERMINAL
    # Every status an order can start in, or reach, can also be left, unless it ends the order.
    reachable = set(lifecycle.INITIAL_STATUS.values()) | {r.to_status for r in TRANSITIONS}
    for status in reachable - lifecycle.TERMINAL:
        assert any(r.from_status == status for r in TRANSITIONS), status


def test_the_database_guard_matches_the_transition_table(db: PgDatabase, shop: Shop) -> None:
    """Every (from, to) pair, for every payment and fulfilment: the database accepts exactly
    the moves the table has."""
    tenant = shop.tenant.value
    with db.admin() as admin:
        orders = {
            (payment, fulfilment, status): insert_order(
                admin, tenant, status=status, payment_method=payment, fulfilment=fulfilment
            )
            for (payment, fulfilment), status in itertools.product(KINDS, STATUSES)
        }
    mismatches = []
    with db.app() as conn, conn.transaction(force_rollback=True):
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        for (payment, fulfilment, start), order_id in orders.items():
            for to in STATUSES:
                if to == start:
                    continue
                expected = find_rule(start, to, payment_method=payment, fulfilment=fulfilment)
                try:
                    with conn.transaction(force_rollback=True):
                        conn.execute(
                            "UPDATE velora.orders SET status = %s, payment_due_at = "
                            "coalesce(payment_due_at, now()) WHERE id = %s",
                            (to, order_id),
                        )
                    accepted = True
                except errors.CheckViolation:
                    accepted = False
                if accepted != (expected is not None):
                    mismatches.append((payment, fulfilment, start, to, accepted))
    assert mismatches == []


def test_illegal_transitions_are_rejected_by_the_engine(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    """Every (from, to, actor) for every payment and fulfilment: `transition` makes exactly
    the moves the table allows that actor, and refuses everything else unchanged."""
    tenant = shop.tenant.value
    staff = staff_id_of(db, shop)
    with db.admin() as admin:
        orders = {
            (payment, fulfilment, status): insert_order(
                admin, tenant, status=status, payment_method=payment, fulfilment=fulfilment
            )
            for (payment, fulfilment), status in itertools.product(KINDS, STATUSES)
        }
    actors = [Actor("customer", uuid.uuid4()), Actor("staff", staff), lifecycle.SYSTEM_ACTOR]

    async def scenario(env: Env) -> None:
        wrong: list[tuple[str, ...]] = []
        async with env.db.tenant(shop.tenant) as conn:
            for (payment, fulfilment, start), order_id in orders.items():
                for to, actor in itertools.product(STATUSES, actors):
                    rule = find_rule(start, to, payment_method=payment, fulfilment=fulfilment)
                    allowed = rule is not None and actor.kind in rule.actors
                    try:
                        async with conn.transaction(force_rollback=True):
                            moved = await transition(
                                conn,
                                shop.tenant,
                                order_id,
                                to=to,
                                actor=actor,
                                now=env.clock.now(),
                                reason="test reason",
                            )
                            assert moved.status == to
                        done = True
                    except IllegalTransitionError:
                        done = False
                    if done != allowed:
                        wrong.append((payment, fulfilment, start, to, actor.kind))
        assert wrong == []

    run(scenario)
    # Every attempt was rolled back or refused: nothing moved.
    found = rows(
        db,
        "SELECT status, payment_method, fulfilment FROM velora.orders WHERE id = ANY(%s)",
        list(orders.values()),
    )
    assert sorted(found) == sorted((s, p, f) for p, f, s in orders)


def test_every_transition_writes_a_history_row_and_an_audit_row(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        await customer.send("YES")
        ((order_id, *_),) = orders_of(db, customer)
        staff = Actor("staff", staff_id_of(db, shop))
        path: list[lifecycle.Status] = ["accepted", "preparing", "out_for_delivery", "completed"]
        async with env.db.tenant(shop.tenant) as conn:
            for to in path:
                env.clock.advance(timedelta(minutes=1))
                await transition(
                    conn, shop.tenant, order_id, to=to, actor=staff, now=env.clock.now()
                )
        history = rows(
            db,
            "SELECT from_status, to_status, actor_kind, actor_staff_id FROM "
            "velora.order_transitions WHERE order_id = %s ORDER BY created_at",
            order_id,
        )
        steps = ["cod_confirmed", *path]
        assert history == [(None, "cod_confirmed", "customer", None)] + [
            (a, b, "staff", staff.id) for a, b in itertools.pairwise(steps)
        ]
        audit = rows(
            db,
            "SELECT action, actor_kind, actor_id, details->>'from', details->>'to' "
            "FROM velora.audit_log WHERE target_id = %s ORDER BY created_at",
            order_id,
        )
        assert [a[0] for a in audit] == ["order.placed"] + ["order.status_changed"] * len(path)
        assert [(a[3], a[4]) for a in audit[1:]] == list(itertools.pairwise(steps))
        assert all(a[1:3] == ("staff", staff.id) for a in audit[1:])
        assert orders_of(db, customer)[0][1] == "completed"

    run(scenario)


def test_a_guarded_move_from_a_stale_status_is_refused(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    with db.admin() as admin:
        order_id = insert_order(admin, shop.tenant.value)
    staff = Actor("staff", staff_id_of(db, shop))

    async def scenario(env: Env) -> None:
        async with env.db.tenant(shop.tenant) as conn:
            await transition(
                conn,
                shop.tenant,
                order_id,
                to="accepted",
                actor=staff,
                now=env.clock.now(),
                expected="cod_confirmed",
            )
        # A second device still shows cod_confirmed and taps Reject.
        async with env.db.tenant(shop.tenant) as conn:
            with pytest.raises(StaleTransitionError):
                await transition(
                    conn,
                    shop.tenant,
                    order_id,
                    to="rejected",
                    actor=staff,
                    now=env.clock.now(),
                    expected="cod_confirmed",
                    reason="out of stock",
                )
            # Without saying what it expected, rejecting an accepted order is illegal anyway.
            with pytest.raises(IllegalTransitionError):
                await transition(
                    conn,
                    shop.tenant,
                    order_id,
                    to="rejected",
                    actor=staff,
                    now=env.clock.now(),
                    reason="out of stock",
                )

    run(scenario)
    assert rows(db, "SELECT status FROM velora.orders WHERE id = %s", order_id) == [("accepted",)]


@pytest.mark.parametrize("to", ["rejected", "cancelled"])
def test_staff_must_say_why_they_refuse_an_order(
    run: Any, db: PgDatabase, shop: Shop, to: lifecycle.Status
) -> None:
    with db.admin() as admin:
        order_id = insert_order(admin, shop.tenant.value)
    staff = Actor("staff", staff_id_of(db, shop))

    async def scenario(env: Env) -> None:
        async with env.db.tenant(shop.tenant) as conn:
            for reason in (None, "   ", "x" * (lifecycle.REASON_MAX + 1)):
                with pytest.raises(InvalidReasonError):
                    await transition(
                        conn,
                        shop.tenant,
                        order_id,
                        to=to,
                        actor=staff,
                        now=env.clock.now(),
                        reason=reason,
                    )

    run(scenario)


def test_a_customer_can_cancel_before_the_order_is_accepted(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        before = slot_count(db, shop)
        (placed,) = await customer.send("YES")
        reference = re.findall(r"#(LS-\d{4})", placed)[0]
        assert slot_count(db, shop) == before + 1
        assert await customer.send("CANCEL") == [
            f"Order #{reference} is cancelled. Reply MENU to start a new order."
        ]
        ((order_id, status, *_),) = orders_of(db, customer)
        assert status == "cancelled"
        assert rows(
            db,
            "SELECT from_status, actor_kind FROM velora.order_transitions WHERE order_id = %s "
            "AND to_status = 'cancelled'",
            order_id,
        ) == [("cod_confirmed", "customer")]
        # Cancelled in the same slot: its capacity place is given back.
        assert slot_count(db, shop) == before
        # Nothing left to cancel.
        assert await customer.send("CANCEL") == [copy.CART_CLEARED]

    run(scenario)


def test_a_customer_cannot_cancel_after_acceptance(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        (placed,) = await customer.send("YES")
        reference = re.findall(r"#(LS-\d{4})", placed)[0]
        ((order_id, *_),) = orders_of(db, customer)
        async with env.db.tenant(shop.tenant) as conn:
            await transition(
                conn,
                shop.tenant,
                order_id,
                to="accepted",
                actor=Actor("staff", staff_id_of(db, shop)),
                now=env.clock.now(),
            )
        customer.new_replies()  # the "accepted" status message
        assert await customer.send("CANCEL") == [copy.cannot_cancel(reference, "accepted")]
        assert orders_of(db, customer)[0][1] == "accepted"

    run(scenario)


def test_cancel_while_building_a_new_cart_keeps_the_placed_order(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        (placed,) = await customer.send("YES")
        reference = re.findall(r"#(LS-\d{4})", placed)[0]
        for text in ("menu", "2", "1"):
            await customer.send(text)
        # The first CANCEL clears the new cart only.
        assert await customer.send("CANCEL") == [copy.open_order_kept(reference)]
        assert orders_of(db, customer)[0][1] == "cod_confirmed"
        # The second cancels the order.
        (cancelled,) = await customer.send("cancel")
        assert cancelled.startswith(f"Order #{reference} is cancelled.")
        assert orders_of(db, customer)[0][1] == "cancelled"

    run(scenario)
