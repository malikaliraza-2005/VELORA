"""Menu endpoints (P4). All need `Permission.MENU_EDIT`; every change is audited."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from velora.api.deps import ServicesDep, require
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.menu.model import Category, Item, Menu, ModifierGroup, Option
from velora.menu.store import LiveMenu, Version

router = APIRouter(prefix="/api/menu")

MenuEditor = Annotated[StaffSession, Depends(require(Permission.MENU_EDIT))]


# Models -----------------------------------------------------------------------------------
# Only shapes and sizes are checked here; the menu rules live in velora.menu.model, so form
# entry and CSV import report problems the same way.


class OptionDoc(BaseModel):
    name: str = Field(max_length=200)
    price_delta_paisa: int


class GroupDoc(BaseModel):
    name: str = Field(max_length=200)
    min_select: int
    max_select: int
    options: list[OptionDoc] = Field(max_length=200)


class ItemDoc(BaseModel):
    sku: str = Field(max_length=100)
    name: str = Field(max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    price_paisa: int
    groups: list[GroupDoc] = Field(default_factory=list, max_length=100)


class CategoryDoc(BaseModel):
    name: str = Field(max_length=200)
    items: list[ItemDoc] = Field(max_length=2000)


class MenuDoc(BaseModel):
    categories: list[CategoryDoc] = Field(max_length=500)


class LiveItemDoc(ItemDoc):
    available: bool


class LiveCategoryDoc(BaseModel):
    name: str
    items: list[LiveItemDoc]


class LiveMenuDoc(BaseModel):
    version_id: uuid.UUID
    version_no: int
    categories: list[LiveCategoryDoc]


class PriceChange(BaseModel):
    key: str
    from_paisa: int
    to_paisa: int


class Changes(BaseModel):
    added_skus: list[str]
    removed_skus: list[str]
    price_changes: list[PriceChange]


class DraftOut(BaseModel):
    """The draft to edit. Without a draft, `version_id` is null, `base_revision` is 0 and the
    menu is a copy of the live one; saving it starts a new draft."""

    version_id: uuid.UUID | None
    version_no: int | None
    base_revision: int
    menu: MenuDoc
    changes_vs_live: Changes


class DraftSave(BaseModel):
    base_revision: int = Field(ge=0)
    menu: MenuDoc


class CsvImport(BaseModel):
    base_revision: int = Field(ge=0)
    csv: str = Field(max_length=1_000_000)


class VersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    revision: int
    status: Literal["draft", "live", "previous"]
    created_at: datetime
    frozen_at: datetime | None


class PublishRequest(BaseModel):
    revision: int = Field(ge=1)


class PublicationOut(BaseModel):
    publication_no: int
    version_no: int
    published_at: datetime
    published_by_staff_id: uuid.UUID


class HistoryOut(BaseModel):
    versions: list[VersionOut]
    publications: list[PublicationOut]


class AvailabilityChange(BaseModel):
    available: bool


# Conversions ------------------------------------------------------------------------------


def _text(value: str | None) -> str | None:
    return value if value else None


def _to_menu(doc: MenuDoc) -> Menu:
    return Menu(
        tuple(
            Category(
                c.name,
                tuple(
                    Item(
                        sku=i.sku.strip().upper(),
                        name=i.name,
                        price_paisa=i.price_paisa,
                        description=_text(i.description),
                        groups=tuple(
                            ModifierGroup(
                                g.name,
                                g.min_select,
                                g.max_select,
                                tuple(Option(o.name, o.price_delta_paisa) for o in g.options),
                            )
                            for g in i.groups
                        ),
                    )
                    for i in c.items
                ),
            )
            for c in doc.categories
        )
    )


def _item_doc(item: Item) -> dict[str, Any]:
    return {
        "sku": item.sku,
        "name": item.name,
        "description": item.description,
        "price_paisa": item.price_paisa,
        "groups": [
            {
                "name": g.name,
                "min_select": g.min_select,
                "max_select": g.max_select,
                "options": [
                    {"name": o.name, "price_delta_paisa": o.price_delta_paisa} for o in g.options
                ],
            }
            for g in item.groups
        ],
    }


def _menu_doc(menu: Menu) -> MenuDoc:
    return MenuDoc.model_validate(
        {
            "categories": [
                {"name": c.name, "items": [_item_doc(i) for i in c.items]} for c in menu.categories
            ]
        }
    )


def _live_doc(live: LiveMenu) -> LiveMenuDoc:
    return LiveMenuDoc.model_validate(
        {
            "version_id": live.version.id,
            "version_no": live.version.version_no,
            "categories": [
                {
                    "name": c.name,
                    "items": [
                        {**_item_doc(i), "available": live.is_available(i.sku)} for i in c.items
                    ],
                }
                for c in live.menu.categories
            ],
        }
    )


def _version_out(version: Version, live_id: uuid.UUID | None) -> VersionOut:
    state: Literal["draft", "live", "previous"] = (
        "draft" if version.is_draft else "live" if version.id == live_id else "previous"
    )
    return VersionOut(
        id=version.id,
        version_no=version.version_no,
        revision=version.revision,
        status=state,
        created_at=version.created_at,
        frozen_at=version.frozen_at,
    )


# Drafts, review and publishing ------------------------------------------------------------


@router.get("/draft")
async def get_draft(session: MenuEditor, services: ServicesDep) -> DraftOut:
    view = await services.menu.draft(session)
    return DraftOut(
        version_id=None if view.version is None else view.version.id,
        version_no=None if view.version is None else view.version.version_no,
        base_revision=view.base_revision,
        menu=_menu_doc(view.menu),
        changes_vs_live=Changes.model_validate(view.changes_vs_live),
    )


@router.put("/draft")
async def save_draft(body: DraftSave, session: MenuEditor, services: ServicesDep) -> VersionOut:
    version = await services.menu.save_draft(
        session, _to_menu(body.menu), base_revision=body.base_revision
    )
    return _version_out(version, None)


@router.post("/draft/import")
async def import_csv(body: CsvImport, session: MenuEditor, services: ServicesDep) -> VersionOut:
    version = await services.menu.import_csv(session, body.csv, base_revision=body.base_revision)
    return _version_out(version, None)


@router.get("/versions")
async def history(session: MenuEditor, services: ServicesDep) -> HistoryOut:
    found = await services.menu.history(session)
    return HistoryOut(
        versions=[_version_out(v, found.live_version_id) for v in found.versions],
        publications=[
            PublicationOut(
                publication_no=p.publication_no,
                version_no=p.version_no,
                published_at=p.published_at,
                published_by_staff_id=p.published_by_staff_id,
            )
            for p in found.publications
        ],
    )


@router.get("/versions/{version_id}")
async def version_menu(
    version_id: uuid.UUID, session: MenuEditor, services: ServicesDep
) -> MenuDoc:
    return _menu_doc(await services.menu.version_menu(session, version_id))


@router.post("/versions/{version_id}/publish")
async def publish(
    version_id: uuid.UUID, body: PublishRequest, session: MenuEditor, services: ServicesDep
) -> VersionOut:
    version = await services.menu.publish(session, version_id, revision=body.revision)
    return _version_out(version, version.id)


# Live menu and availability ---------------------------------------------------------------


@router.get("/live")
async def live(session: MenuEditor, services: ServicesDep) -> LiveMenuDoc:
    found = await services.menu.live(session)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no menu has been published yet")
    return _live_doc(found)


@router.put("/availability/{sku}", status_code=status.HTTP_204_NO_CONTENT)
async def set_availability(
    sku: str, body: AvailabilityChange, session: MenuEditor, services: ServicesDep
) -> None:
    await services.menu.set_availability(session, sku, available=body.available)
