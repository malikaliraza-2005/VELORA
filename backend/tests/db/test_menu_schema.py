"""P4: database guards for the catalogue, run as the runtime and platform roles.

- A published (frozen) menu version and its rows can't be changed, so a live menu can't be
  edited in place and an old one can be re-published exactly as it was.
- Only a frozen version can be published, and the founder's platform role can't publish or
  freeze anything: a draft never goes live by accident.
- Prices can't be negative; SKUs are unique within a version.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.db.conftest import PgDatabase, SeededTenants


def _as_tenant(conn: psycopg.Connection[Any], tenant: UUID) -> None:
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))


def _draft(conn: psycopg.Connection[Any], tenants: SeededTenants) -> UUID:
    row = conn.execute(
        "INSERT INTO velora.menu_versions (tenant_id, version_no, created_by_kind, "
        "created_by_staff_id) VALUES (%s, 2, 'staff', %s) RETURNING id",
        (tenants.a["tenants"], tenants.a["staff_members"]),
    ).fetchone()
    assert row is not None
    value: UUID = row[0]
    return value


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM velora.modifier_options WHERE id = %(option)s",
        "DELETE FROM velora.menu_categories WHERE menu_version_id = %(version)s",
        "INSERT INTO velora.menu_categories (tenant_id, menu_version_id, position, name) "
        "VALUES (%(tenant)s, %(version)s, 1, 'Sneaked in')",
        "UPDATE velora.menu_versions SET frozen_at = NULL WHERE id = %(version)s",
        "UPDATE velora.menu_versions SET revision = revision + 1 WHERE id = %(version)s",
    ],
)
def test_a_published_version_cannot_change(
    db: PgDatabase, tenants: SeededTenants, statement: str
) -> None:
    params = {
        "tenant": tenants.a["tenants"],
        "version": tenants.a["menu_versions"],
        "option": tenants.a["modifier_options"],
    }
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        with pytest.raises(errors.RestrictViolation, match="published menu version"):
            conn.execute(statement.encode(), params)


def test_frozen_rows_resist_even_the_administrator(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.admin() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        with pytest.raises(errors.RestrictViolation):
            conn.execute(
                "UPDATE velora.menu_items SET price_paisa = 1 WHERE id = %s",
                (tenants.a["menu_items"],),
            )


def test_only_a_frozen_version_can_be_published(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        draft = _draft(conn, tenants)
        with pytest.raises(errors.CheckViolation, match="only a frozen menu version"):
            conn.execute(
                "INSERT INTO velora.menu_publications (tenant_id, publication_no, "
                "menu_version_id, published_by_staff_id) VALUES (%s, 2, %s, %s)",
                (tenants.a["tenants"], draft, tenants.a["staff_members"]),
            )


def test_one_draft_per_tenant(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        _draft(conn, tenants)
        with pytest.raises(errors.UniqueViolation):
            conn.execute(
                "INSERT INTO velora.menu_versions (tenant_id, version_no, created_by_kind, "
                "created_by_staff_id) VALUES (%s, 3, 'staff', %s)",
                (tenants.a["tenants"], tenants.a["staff_members"]),
            )


def test_publications_are_append_only_for_everyone(db: PgDatabase, tenants: SeededTenants) -> None:
    with (
        db.admin() as conn,
        conn.transaction(force_rollback=True),
        pytest.raises(errors.RestrictViolation),
    ):
        conn.execute(
            "DELETE FROM velora.menu_publications WHERE id = %s",
            (tenants.a["menu_publications"],),
        )


@pytest.mark.parametrize(
    ("table", "column"),
    [("menu_items", "price_paisa"), ("modifier_options", "price_delta_paisa")],
)
def test_prices_cannot_be_negative(
    db: PgDatabase, tenants: SeededTenants, table: str, column: str
) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        draft = _draft(conn, tenants)
        category = conn.execute(
            "INSERT INTO velora.menu_categories (tenant_id, menu_version_id, position, name) "
            "VALUES (%s, %s, 0, 'C') RETURNING id",
            (tenants.a["tenants"], draft),
        ).fetchone()
        assert category is not None
        price = -1 if table == "menu_items" else 0
        item = conn.execute(
            "INSERT INTO velora.menu_items (tenant_id, menu_version_id, category_id, position, "
            "sku, name, price_paisa) VALUES (%s, %s, %s, 0, 'X1', 'X', 0) RETURNING id",
            (tenants.a["tenants"], draft, category[0]),
        ).fetchone()
        assert item is not None
        with pytest.raises(errors.CheckViolation, match=column):
            if table == "menu_items":
                conn.execute(
                    "INSERT INTO velora.menu_items (tenant_id, menu_version_id, category_id, "
                    "position, sku, name, price_paisa) VALUES (%s, %s, %s, 1, 'X2', 'X', %s)",
                    (tenants.a["tenants"], draft, category[0], price),
                )
            else:
                group = conn.execute(
                    "INSERT INTO velora.modifier_groups (tenant_id, menu_version_id, item_id, "
                    "position, name, min_select, max_select) VALUES (%s, %s, %s, 0, 'G', 0, 1) "
                    "RETURNING id",
                    (tenants.a["tenants"], draft, item[0]),
                ).fetchone()
                assert group is not None
                conn.execute(
                    "INSERT INTO velora.modifier_options (tenant_id, menu_version_id, group_id, "
                    "position, name, price_delta_paisa) VALUES (%s, %s, %s, 0, 'O', -1)",
                    (tenants.a["tenants"], draft, group[0]),
                )


def test_skus_are_unique_within_a_version(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.app() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        draft = _draft(conn, tenants)
        row = conn.execute(
            "INSERT INTO velora.menu_categories (tenant_id, menu_version_id, position, name) "
            "VALUES (%s, %s, 0, 'C') RETURNING id",
            (tenants.a["tenants"], draft),
        ).fetchone()
        assert row is not None
        insert = (
            "INSERT INTO velora.menu_items (tenant_id, menu_version_id, category_id, position, "
            "sku, name, price_paisa) VALUES (%s, %s, %s, 0, 'BURGER-1', 'X', 100)"
        )
        conn.execute(insert, (tenants.a["tenants"], draft, row[0]))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(insert, (tenants.a["tenants"], draft, row[0]))


def test_the_platform_role_cannot_publish_or_freeze(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.platform() as conn:
        with conn.transaction(force_rollback=True):
            _as_tenant(conn, tenants.a["tenants"])
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO velora.menu_publications (tenant_id, publication_no, "
                    "menu_version_id, published_by_staff_id) VALUES (%s, 9, %s, %s)",
                    (tenants.a["tenants"], tenants.a["menu_versions"], tenants.a["staff_members"]),
                )
        with conn.transaction(force_rollback=True):
            _as_tenant(conn, tenants.a["tenants"])
            draft = conn.execute(
                "INSERT INTO velora.menu_versions (tenant_id, version_no, created_by_kind) "
                "VALUES (%s, 2, 'platform') RETURNING id",
                (tenants.a["tenants"],),
            ).fetchone()
            assert draft is not None
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE velora.menu_versions SET frozen_at = now() WHERE id = %s", (draft[0],)
                )


def test_the_platform_role_sees_one_tenant_only(db: PgDatabase, tenants: SeededTenants) -> None:
    with db.platform() as conn, conn.transaction(force_rollback=True):
        _as_tenant(conn, tenants.a["tenants"])
        rows = conn.execute("SELECT DISTINCT tenant_id FROM velora.menu_items").fetchall()
    assert rows == [(tenants.a["tenants"],)]
