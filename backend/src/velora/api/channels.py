"""The owner's WhatsApp endpoints (P9-T6). All need `CHANNELS_MANAGE` (owner only).

- `GET /api/channels`: each channel's state, for the status banner.
- `POST /api/channels/{id}/relink`: start pairing (audited).
- `GET /api/channels/{id}/qr.svg`: the pairing QR code as an SVG image, `no-store`; 204 when
  the provider has none (already paired, or not ready yet). The code is never logged.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime
from typing import Annotated

import segno
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from velora.api.deps import ServicesDep, require
from velora.identity.permissions import Permission
from velora.identity.sessions import StaffSession
from velora.messaging.channel_admin import ChannelStatus

router = APIRouter(prefix="/api/channels")

ChannelManager = Annotated[StaffSession, Depends(require(Permission.CHANNELS_MANAGE))]


class ChannelOut(BaseModel):
    id: uuid.UUID
    provider: str
    state: str
    state_changed_at: datetime | None
    adapter_running: bool


def _out(found: ChannelStatus) -> ChannelOut:
    return ChannelOut.model_validate(found, from_attributes=True)


@router.get("")
async def list_channels(session: ChannelManager, services: ServicesDep) -> list[ChannelOut]:
    return [_out(c) for c in await services.channels.list(session)]


@router.post("/{channel_id}/relink")
async def relink(
    channel_id: uuid.UUID, session: ChannelManager, services: ServicesDep
) -> ChannelOut:
    return _out(await services.channels.relink(session, channel_id))


@router.get("/{channel_id}/qr.svg", response_class=Response)
async def qr(channel_id: uuid.UUID, session: ChannelManager, services: ServicesDep) -> Response:
    headers = {"Cache-Control": "no-store"}
    code = await services.channels.qr(session, channel_id)
    if code is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers=headers)
    out = io.BytesIO()
    segno.make_qr(code, error="m").save(out, kind="svg", scale=6, border=4, xmldecl=False)
    return Response(out.getvalue(), media_type="image/svg+xml", headers=headers)
