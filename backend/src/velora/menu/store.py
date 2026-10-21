"""Reading and writing menu versions (P4-T1, P4-T3, P4-T4).

All functions take a connection already scoped to one tenant (`Database.tenant()`, or the
founder tool's tenant transaction), so RLS limits them to that tenant.

- `save_draft` replaces the draft's whole content, creating the draft if there is none, and
  audits the save with its price changes.
- `live_menu` is the customer-facing read: only the newest *published* version, with the
  current availability. A draft is never visible through it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from psycopg import AsyncConnection, errors, sql

from velora.db import TenantId
from velora.identity import audit
from velora.identity.errors import ConflictError, expect_row
from velora.menu.model import Category, Item, Menu, ModifierGroup, Option, price_changes


@dataclass(frozen=True)
class Editor:
    """Who is changing a menu: a signed-in staff member, or the founder's onboarding tool."""

    kind: Literal["staff", "platform"]
    staff_id: uuid.UUID | None = None
    operator: str | None = None

    def audit_details(self, details: dict[str, Any]) -> dict[str, Any]:
        return {"operator": self.operator, **details} if self.kind == "platform" else details


@dataclass(frozen=True)
class Version:
    id: uuid.UUID
    version_no: int
    revision: int
    created_at: datetime
    frozen_at: datetime | None

    @property
    def is_draft(self) -> bool:
        return self.frozen_at is None


@dataclass(frozen=True)
class LiveMenu:
    """The published menu with today's availability."""

    version: Version
    menu: Menu
    unavailable: frozenset[str]

    def is_available(self, sku: str) -> bool:
        return sku not in self.unavailable

    def offerable(self) -> Menu:
        """What a customer may be offered: unavailable items and empty categories removed."""
        categories = []
        for category in self.menu.categories:
            items = tuple(i for i in category.items if self.is_available(i.sku))
            if items:
                categories.append(replace(category, items=items))
        return Menu(tuple(categories))

    def item(self, sku: str) -> Item | None:
        return next((i for i in self.menu.items() if i.sku == sku), None)


def _version(row: tuple[Any, ...]) -> Version:
    return Version(row[0], row[1], row[2], row[3], row[4])


def _for_update(lock: bool) -> sql.SQL:
    return sql.SQL(" FOR UPDATE" if lock else "")


async def get_version(
    conn: AsyncConnection[Any], version_id: uuid.UUID, *, lock: bool = False
) -> Version | None:
    cur = await conn.execute(
        sql.SQL(
            "SELECT id, version_no, revision, created_at, frozen_at FROM velora.menu_versions "
            "WHERE id = %s{}"
        ).format(_for_update(lock)),
        (version_id,),
    )
    row = await cur.fetchone()
    return None if row is None else _version(row)


async def get_draft(conn: AsyncConnection[Any], *, lock: bool = False) -> Version | None:
    cur = await conn.execute(
        sql.SQL(
            "SELECT id, version_no, revision, created_at, frozen_at FROM velora.menu_versions "
            "WHERE frozen_at IS NULL{}"
        ).format(_for_update(lock))
    )
    row = await cur.fetchone()
    return None if row is None else _version(row)


async def list_versions(conn: AsyncConnection[Any]) -> list[Version]:
    cur = await conn.execute(
        "SELECT id, version_no, revision, created_at, frozen_at FROM velora.menu_versions "
        "ORDER BY version_no DESC"
    )
    return [_version(row) for row in await cur.fetchall()]


async def get_live_version(conn: AsyncConnection[Any]) -> Version | None:
    """The version of the newest publication, or None if nothing was ever published."""
    cur = await conn.execute(
        "SELECT v.id, v.version_no, v.revision, v.created_at, v.frozen_at "
        "FROM velora.menu_publications p JOIN velora.menu_versions v "
        "ON v.tenant_id = p.tenant_id AND v.id = p.menu_version_id "
        "ORDER BY p.publication_no DESC LIMIT 1"
    )
    row = await cur.fetchone()
    return None if row is None else _version(row)


async def load_menu(conn: AsyncConnection[Any], version_id: uuid.UUID) -> Menu:
    cur = await conn.execute(
        "SELECT id, name FROM velora.menu_categories WHERE menu_version_id = %s "
        "ORDER BY position, name",
        (version_id,),
    )
    categories = await cur.fetchall()
    cur = await conn.execute(
        "SELECT id, category_id, sku, name, description, price_paisa FROM velora.menu_items "
        "WHERE menu_version_id = %s ORDER BY position, sku",
        (version_id,),
    )
    items = await cur.fetchall()
    cur = await conn.execute(
        "SELECT id, item_id, name, min_select, max_select FROM velora.modifier_groups "
        "WHERE menu_version_id = %s ORDER BY position, name",
        (version_id,),
    )
    groups = await cur.fetchall()
    cur = await conn.execute(
        "SELECT group_id, name, price_delta_paisa FROM velora.modifier_options "
        "WHERE menu_version_id = %s ORDER BY position, name",
        (version_id,),
    )
    options: dict[uuid.UUID, list[Option]] = {}
    for group_id, name, delta in await cur.fetchall():
        options.setdefault(group_id, []).append(Option(name, delta))
    item_groups: dict[uuid.UUID, list[ModifierGroup]] = {}
    for gid, item_id, name, min_select, max_select in groups:
        item_groups.setdefault(item_id, []).append(
            ModifierGroup(name, min_select, max_select, tuple(options.get(gid, [])))
        )
    category_items: dict[uuid.UUID, list[Item]] = {}
    for iid, category_id, sku, name, description, price in items:
        category_items.setdefault(category_id, []).append(
            Item(sku, name, price, description, tuple(item_groups.get(iid, [])))
        )
    return Menu(
        tuple(Category(name, tuple(category_items.get(cid, []))) for cid, name in categories)
    )


async def unavailable_skus(conn: AsyncConnection[Any]) -> frozenset[str]:
    cur = await conn.execute("SELECT sku FROM velora.menu_item_availability WHERE NOT is_available")
    return frozenset(row[0] for row in await cur.fetchall())


async def live_menu(conn: AsyncConnection[Any]) -> LiveMenu | None:
    """The customer-facing menu, or None if the tenant has never published one."""
    version = await get_live_version(conn)
    if version is None:
        return None
    return LiveMenu(version, await load_menu(conn, version.id), await unavailable_skus(conn))


async def save_draft(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    menu: Menu,
    *,
    editor: Editor,
    base_revision: int | None,
    now: datetime,
    action: str,
    details: dict[str, Any] | None = None,
) -> Version:
    """Replace the draft's content with `menu` (already validated) and audit it.

    `base_revision` is the draft revision the editor started from (0 when there was no
    draft). A mismatch means someone else saved or published meanwhile: ConflictError.
    None skips the check (the founder's import overwrites the draft on purpose).
    """
    draft = await get_draft(conn, lock=True)
    current = 0 if draft is None else draft.revision
    if base_revision is not None and base_revision != current:
        raise ConflictError(
            "the draft menu changed since you opened it; reload it and apply your changes again"
        )
    if draft is None:
        # A new draft's changes are reported against the live menu (or the newest version).
        basis = await get_live_version(conn) or _latest(await list_versions(conn))
        before = None if basis is None else await load_menu(conn, basis.id)
        draft = await _create_draft(conn, tenant, editor, now)
    else:
        before = await load_menu(conn, draft.id)
        for table in ("modifier_options", "modifier_groups", "menu_items", "menu_categories"):
            await conn.execute(
                sql.SQL("DELETE FROM velora.{} WHERE menu_version_id = %s").format(
                    sql.Identifier(table)
                ),
                (draft.id,),
            )
        cur = await conn.execute(
            "UPDATE velora.menu_versions SET revision = revision + 1, updated_at = %s "
            "WHERE id = %s RETURNING id, version_no, revision, created_at, frozen_at",
            (now, draft.id),
        )
        draft = _version(expect_row(await cur.fetchone()))
    await _insert_content(conn, tenant, draft.id, menu)
    await audit.record(
        conn,
        tenant,
        at=now,
        actor_kind=editor.kind,
        actor_id=editor.staff_id,
        action=action,
        target_type="menu_version",
        target_id=draft.id,
        details=editor.audit_details(
            {
                "version_no": draft.version_no,
                "revision": draft.revision,
                "items": menu.item_count(),
                **price_changes(before, menu),
                **(details or {}),
            }
        ),
    )
    return draft


def _latest(versions: list[Version]) -> Version | None:
    return versions[0] if versions else None


async def _create_draft(
    conn: AsyncConnection[Any], tenant: TenantId, editor: Editor, now: datetime
) -> Version:
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO velora.menu_versions (tenant_id, version_no, created_by_kind, "
                "created_by_staff_id, created_at, updated_at) "
                "SELECT %s, coalesce(max(version_no), 0) + 1, %s, %s, %s, %s "
                "FROM velora.menu_versions "
                "RETURNING id, version_no, revision, created_at, frozen_at",
                (tenant.value, editor.kind, editor.staff_id, now, now),
            )
            return _version(expect_row(await cur.fetchone()))
    except errors.UniqueViolation as exc:
        raise ConflictError(
            "someone else started a new draft at the same time; reload and try again"
        ) from exc


async def _insert_content(
    conn: AsyncConnection[Any], tenant: TenantId, version_id: uuid.UUID, menu: Menu
) -> None:
    t, v = tenant.value, version_id
    categories, items, groups, options = [], [], [], []
    for cpos, category in enumerate(menu.categories):
        cid = uuid.uuid4()
        categories.append((cid, t, v, cpos, category.name))
        for ipos, item in enumerate(category.items):
            iid = uuid.uuid4()
            items.append(
                (iid, t, v, cid, ipos, item.sku, item.name, item.description, item.price_paisa)
            )
            for gpos, group in enumerate(item.groups):
                gid = uuid.uuid4()
                groups.append(
                    (gid, t, v, iid, gpos, group.name, group.min_select, group.max_select)
                )
                for opos, option in enumerate(group.options):
                    options.append((t, v, gid, opos, option.name, option.price_delta_paisa))
    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO velora.menu_categories (id, tenant_id, menu_version_id, position, name) "
            "VALUES (%s, %s, %s, %s, %s)",
            categories,
        )
        await cur.executemany(
            "INSERT INTO velora.menu_items (id, tenant_id, menu_version_id, category_id, "
            "position, sku, name, description, price_paisa) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            items,
        )
        await cur.executemany(
            "INSERT INTO velora.modifier_groups (id, tenant_id, menu_version_id, item_id, "
            "position, name, min_select, max_select) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            groups,
        )
        await cur.executemany(
            "INSERT INTO velora.modifier_options (tenant_id, menu_version_id, group_id, "
            "position, name, price_delta_paisa) VALUES (%s, %s, %s, %s, %s, %s)",
            options,
        )
