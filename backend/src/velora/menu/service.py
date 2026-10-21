"""Staff menu operations (P4-T1 to P4-T4). All need `Permission.MENU_EDIT` (owner, manager).

- Form entry and CSV import both replace the whole draft, after the same validation.
- Publishing names a version and the revision the reviewer saw. A draft is frozen when it is
  first published; re-publishing an older frozen version is the rollback.
- Every change is audited in its own transaction, including the price changes it makes.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg import errors

from velora.clock import Clock
from velora.db import Database
from velora.identity import audit
from velora.identity.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationProblemsError,
    expect_row,
)
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.menu import store
from velora.menu.csv_import import CsvImportError, parse_menu_csv
from velora.menu.model import SKU_PATTERN, Menu, Problem, price_changes, validate_menu
from velora.menu.store import Editor, LiveMenu, Version


@dataclass(frozen=True)
class DraftView:
    """What an editor starts from. Without a draft: the live menu and base revision 0."""

    version: Version | None
    base_revision: int
    menu: Menu
    changes_vs_live: dict[str, object]


@dataclass(frozen=True)
class Publication:
    publication_no: int
    version_no: int
    published_at: datetime
    published_by_staff_id: uuid.UUID


@dataclass(frozen=True)
class History:
    versions: list[Version]
    publications: list[Publication]
    live_version_id: uuid.UUID | None


def _require(actor: StaffSession) -> None:
    if not actor.can(Permission.MENU_EDIT):
        raise ForbiddenError(f"your role can't do this ({Permission.MENU_EDIT})")


def _editor(actor: StaffSession) -> Editor:
    return Editor("staff", actor.staff_id)


def _invalid(problems: list[Problem]) -> ValidationProblemsError:
    return ValidationProblemsError(
        f"the menu has {len(problems)} problem(s)", [p.as_dict() for p in problems]
    )


class MenuService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    # Drafts -------------------------------------------------------------------------------

    async def draft(self, actor: StaffSession) -> DraftView:
        _require(actor)
        async with self._db.tenant(actor.tenant) as conn:
            draft = await store.get_draft(conn)
            live = await store.get_live_version(conn)
            live_menu = None if live is None else await store.load_menu(conn, live.id)
            if draft is None:
                menu = live_menu or Menu(())
                return DraftView(None, 0, menu, price_changes(live_menu, menu))
            menu = await store.load_menu(conn, draft.id)
        return DraftView(draft, draft.revision, menu, price_changes(live_menu, menu))

    async def save_draft(self, actor: StaffSession, menu: Menu, *, base_revision: int) -> Version:
        """Form entry: replace the draft with `menu`."""
        _require(actor)
        problems = validate_menu(menu)
        if problems:
            raise _invalid(problems)
        async with self._db.tenant(actor.tenant) as conn:
            return await store.save_draft(
                conn,
                actor.tenant,
                menu,
                editor=_editor(actor),
                base_revision=base_revision,
                now=self._clock.now(),
                action="menu.draft_saved",
                details={"source": "form"},
            )

    async def import_csv(self, actor: StaffSession, data: str, *, base_revision: int) -> Version:
        """CSV import: replace the draft with the file's menu, or report every problem."""
        _require(actor)
        try:
            menu = parse_menu_csv(data)
        except CsvImportError as exc:
            raise _invalid(exc.problems) from exc
        async with self._db.tenant(actor.tenant) as conn:
            return await store.save_draft(
                conn,
                actor.tenant,
                menu,
                editor=_editor(actor),
                base_revision=base_revision,
                now=self._clock.now(),
                action="menu.draft_imported",
                details={
                    "source": "csv",
                    "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
                },
            )

    # Publishing ---------------------------------------------------------------------------

    async def publish(
        self, actor: StaffSession, version_id: uuid.UUID, *, revision: int
    ) -> Version:
        """Put a version live. A draft must be at `revision`, the one the reviewer saw."""
        _require(actor)
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            version = await store.get_version(conn, version_id, lock=True)
            if version is None:
                raise NotFoundError("no such menu version")
            if version.revision != revision:
                raise ConflictError(
                    f"this version changed since you reviewed it (revision {version.revision}, "
                    f"you reviewed {revision}); review it again before publishing"
                )
            live = await store.get_live_version(conn)
            if live is not None and live.id == version.id:
                raise ConflictError("this version is already live")
            menu = await store.load_menu(conn, version.id)
            was_draft = version.is_draft
            if was_draft:
                problems = validate_menu(menu, for_publish=True)
                if problems:
                    raise _invalid(problems)
                cur = await conn.execute(
                    "UPDATE velora.menu_versions SET frozen_at = %s, updated_at = %s "
                    "WHERE id = %s RETURNING id, version_no, revision, created_at, frozen_at",
                    (now, now, version.id),
                )
                row = expect_row(await cur.fetchone())
                version = Version(row[0], row[1], row[2], row[3], row[4])
            before = None if live is None else await store.load_menu(conn, live.id)
            try:
                await conn.execute(
                    "INSERT INTO velora.menu_publications (tenant_id, publication_no, "
                    "menu_version_id, published_by_staff_id, published_at) "
                    "SELECT %s, coalesce(max(publication_no), 0) + 1, %s, %s, %s "
                    "FROM velora.menu_publications",
                    (actor.tenant.value, version.id, actor.staff_id, now),
                )
            except errors.UniqueViolation as exc:
                raise ConflictError("someone else published at the same time; reload") from exc
            await audit.record(
                conn,
                actor.tenant,
                at=now,
                actor_kind="staff",
                actor_id=actor.staff_id,
                action="menu.published",
                target_type="menu_version",
                target_id=version.id,
                details={
                    "version_no": version.version_no,
                    "revision": version.revision,
                    "republished": not was_draft,
                    "previous_version_no": None if live is None else live.version_no,
                    **price_changes(before, menu),
                },
            )
        return version

    async def history(self, actor: StaffSession) -> History:
        _require(actor)
        async with self._db.tenant(actor.tenant) as conn:
            versions = await store.list_versions(conn)
            live = await store.get_live_version(conn)
            cur = await conn.execute(
                "SELECT p.publication_no, v.version_no, p.published_at, p.published_by_staff_id "
                "FROM velora.menu_publications p JOIN velora.menu_versions v "
                "ON v.tenant_id = p.tenant_id AND v.id = p.menu_version_id "
                "ORDER BY p.publication_no DESC"
            )
            publications = [Publication(*row) for row in await cur.fetchall()]
        return History(versions, publications, None if live is None else live.id)

    async def version_menu(self, actor: StaffSession, version_id: uuid.UUID) -> Menu:
        _require(actor)
        async with self._db.tenant(actor.tenant) as conn:
            if await store.get_version(conn, version_id) is None:
                raise NotFoundError("no such menu version")
            return await store.load_menu(conn, version_id)

    # Availability (P4-T4) -----------------------------------------------------------------

    async def live(self, actor: StaffSession) -> LiveMenu | None:
        """The live menu with availability, as staff see it (unavailable items included)."""
        _require(actor)
        async with self._db.tenant(actor.tenant) as conn:
            return await store.live_menu(conn)

    async def set_availability(self, actor: StaffSession, sku: str, *, available: bool) -> None:
        _require(actor)
        sku = sku.strip().upper()
        if not SKU_PATTERN.match(sku):
            raise NotFoundError("no such item")
        now = self._clock.now()
        async with self._db.tenant(actor.tenant) as conn:
            # Staff may mark a draft-only item in advance, so it goes live already unavailable.
            live = await store.get_live_version(conn)
            cur = await conn.execute(
                "SELECT 1 FROM velora.menu_items i JOIN velora.menu_versions v "
                "ON v.tenant_id = i.tenant_id AND v.id = i.menu_version_id "
                "WHERE i.sku = %s AND (v.frozen_at IS NULL OR v.id = %s) LIMIT 1",
                (sku, None if live is None else live.id),
            )
            if await cur.fetchone() is None:
                raise NotFoundError("no item with this SKU on the live or draft menu")
            cur = await conn.execute(
                "SELECT is_available FROM velora.menu_item_availability WHERE sku = %s FOR UPDATE",
                (sku,),
            )
            row = await cur.fetchone()
            if (row[0] if row is not None else True) is available:
                return
            await conn.execute(
                "INSERT INTO velora.menu_item_availability "
                "(tenant_id, sku, is_available, updated_by_staff_id, updated_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tenant_id, sku) DO UPDATE SET "
                "is_available = EXCLUDED.is_available, "
                "updated_by_staff_id = EXCLUDED.updated_by_staff_id, "
                "updated_at = EXCLUDED.updated_at",
                (actor.tenant.value, sku, available, actor.staff_id, now),
            )
            await audit.record(
                conn,
                actor.tenant,
                at=now,
                actor_kind="staff",
                actor_id=actor.staff_id,
                action="menu.availability_changed",
                target_type="menu_item",
                details={"sku": sku, "available": available},
            )
