"""Staff identity endpoints (P3). Every state-changing endpoint writes an audit row."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from velora.api.deps import SESSION_COOKIE, ServicesDep, SessionDep, check_origin, require
from velora.identity.permissions import MATRIX, Permission, Role
from velora.identity.sessions import SESSION_ABSOLUTE_LIFETIME, IssuedLink, StaffSession
from velora.identity.staff import StaffMember
from velora.identity.tokens import csrf_token_for

router = APIRouter(prefix="/api")

StaffViewer = Annotated[StaffSession, Depends(require(Permission.STAFF_VIEW))]
StaffManager = Annotated[StaffSession, Depends(require(Permission.STAFF_MANAGE))]
SettingsEditor = Annotated[StaffSession, Depends(require(Permission.SETTINGS_EDIT))]


# Models -----------------------------------------------------------------------------------


class LoginRequest(BaseModel):
    token: str = Field(max_length=200)
    device_label: str | None = Field(default=None, max_length=100)


class Me(BaseModel):
    staff_id: uuid.UUID
    display_name: str
    role: Role
    permissions: list[Permission]
    csrf_token: str


class StaffOut(BaseModel):
    id: uuid.UUID
    display_name: str
    role: Role
    active: bool
    has_pin: bool


class LinkOut(BaseModel):
    """A magic link for the owner to pass on by hand (B7: manual delivery in the pilot)."""

    url: str
    expires_at: datetime


class InviteRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    role: Role


class InviteOut(BaseModel):
    staff: StaffOut
    link: LinkOut


class RoleChange(BaseModel):
    role: Role


class PinChange(BaseModel):
    current_pin: str | None = Field(default=None, max_length=12)
    new_pin: str = Field(max_length=12)


class SettingsChange(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class Revoked(BaseModel):
    sessions_revoked: int


def _me(session: StaffSession) -> Me:
    return Me(
        staff_id=session.staff_id,
        display_name=session.display_name,
        role=session.role,
        permissions=sorted(MATRIX[session.role]),
        csrf_token=csrf_token_for(session.token),
    )


def _staff(member: StaffMember) -> StaffOut:
    return StaffOut(
        id=member.id,
        display_name=member.display_name,
        role=member.role,
        active=member.active,
        has_pin=member.has_pin,
    )


def _link(services: ServicesDep, link: IssuedLink) -> LinkOut:
    return LinkOut(
        url=services.settings.magic_link_url(link.token.wire()), expires_at=link.expires_at
    )


# Login and sessions -----------------------------------------------------------------------


@router.post("/auth/login")
async def login(
    body: LoginRequest, request: Request, response: Response, services: ServicesDep
) -> Me:
    check_origin(request, services.settings)
    client = request.client.host if request.client else "unknown"
    if not services.login_limiter.allow(client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts")
    session = await services.sessions.login(body.token, device_label=body.device_label)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "this link is invalid or has expired")
    response.set_cookie(
        SESSION_COOKIE,
        session.token.wire(),
        max_age=int(SESSION_ABSOLUTE_LIFETIME.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return _me(session)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(session: SessionDep, services: ServicesDep, response: Response) -> None:
    await services.sessions.logout(session)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


@router.post("/auth/logout-all")
async def logout_all(session: SessionDep, services: ServicesDep, response: Response) -> Revoked:
    count = await services.sessions.logout_all(session)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return Revoked(sessions_revoked=count)


@router.get("/me")
async def me(session: SessionDep) -> Me:
    return _me(session)


@router.put("/me/pin", status_code=status.HTTP_204_NO_CONTENT)
async def set_pin(body: PinChange, session: SessionDep, services: ServicesDep) -> None:
    await services.staff.set_own_pin(session, current_pin=body.current_pin, new_pin=body.new_pin)


# Staff management (owner) -----------------------------------------------------------------


@router.get("/staff")
async def list_staff(session: StaffViewer, services: ServicesDep) -> list[StaffOut]:
    return [_staff(m) for m in await services.staff.list_staff(session)]


@router.post("/staff", status_code=status.HTTP_201_CREATED)
async def invite_staff(
    body: InviteRequest, session: StaffManager, services: ServicesDep
) -> InviteOut:
    member, link = await services.staff.invite(
        session, display_name=body.display_name, role=body.role
    )
    return InviteOut(staff=_staff(member), link=_link(services, link))


@router.post("/staff/{staff_id}/login-link", status_code=status.HTTP_201_CREATED)
async def issue_login_link(
    staff_id: uuid.UUID, session: StaffManager, services: ServicesDep
) -> LinkOut:
    return _link(services, await services.staff.issue_login_link(session, staff_id))


@router.patch("/staff/{staff_id}")
async def change_role(
    staff_id: uuid.UUID, body: RoleChange, session: StaffManager, services: ServicesDep
) -> StaffOut:
    return _staff(await services.staff.change_role(session, staff_id, body.role))


@router.delete("/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_staff(staff_id: uuid.UUID, session: StaffManager, services: ServicesDep) -> None:
    await services.staff.remove(session, staff_id)


@router.post("/staff/{staff_id}/revoke-sessions")
async def revoke_sessions(
    staff_id: uuid.UUID, session: StaffManager, services: ServicesDep
) -> Revoked:
    return Revoked(sessions_revoked=await services.staff.revoke_sessions(session, staff_id))


@router.post("/staff/{staff_id}/pin-reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_pin(staff_id: uuid.UUID, session: StaffManager, services: ServicesDep) -> None:
    await services.staff.reset_pin(session, staff_id)


# Settings ---------------------------------------------------------------------------------


@router.patch("/settings", status_code=status.HTTP_204_NO_CONTENT)
async def update_settings(
    body: SettingsChange, session: SettingsEditor, services: ServicesDep
) -> None:
    await services.staff.update_settings(session, display_name=body.display_name)
