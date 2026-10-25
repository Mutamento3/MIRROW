"""Local control surface for the Rift Archive co-play integration."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rift_archive import get_rift_archive_service
from rift_archive.client import RiftMcpError


router = APIRouter(prefix="/api/rift-archive", tags=["rift-archive"])


class PairBody(BaseModel):
    invite_text: str = Field(min_length=20, max_length=4000)
    interaction_mode: Literal["partner", "consult"] = "partner"


class PlayModeBody(BaseModel):
    enabled: bool


class InteractionModeBody(BaseModel):
    mode: Literal["partner", "consult"]


def _bad_request(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc)[:240])
    if isinstance(exc, RiftMcpError):
        return HTTPException(status_code=502, detail="裂隙档案服务暂时没有响应，请稍后刷新")
    return HTTPException(status_code=500, detail="裂隙档案本地接入失败")


@router.get("/status")
async def get_status() -> dict[str, Any]:
    return get_rift_archive_service().status()


@router.post("/pair")
async def pair_game(body: PairBody) -> dict[str, Any]:
    try:
        return await get_rift_archive_service().pair(
            body.invite_text, interaction_mode=body.interaction_mode
        )
    except Exception as exc:
        raise _bad_request(exc) from None


@router.post("/refresh")
async def refresh_game() -> dict[str, Any]:
    try:
        state = await get_rift_archive_service().refresh()
        return {"success": True, "state": state, "status": get_rift_archive_service().status()}
    except Exception as exc:
        raise _bad_request(exc) from None


@router.put("/play-mode")
async def update_play_mode(body: PlayModeBody) -> dict[str, Any]:
    try:
        return get_rift_archive_service().set_play_mode(body.enabled)
    except Exception as exc:
        raise _bad_request(exc) from None


@router.put("/interaction-mode")
async def update_interaction_mode(body: InteractionModeBody) -> dict[str, Any]:
    try:
        return get_rift_archive_service().set_interaction_mode(body.mode)
    except Exception as exc:
        raise _bad_request(exc) from None


@router.get("/archive")
async def get_archive() -> dict[str, Any]:
    try:
        return await get_rift_archive_service().read_archive()
    except Exception as exc:
        raise _bad_request(exc) from None


@router.post("/invite-card", response_model=None)
async def get_invite_card() -> JSONResponse:
    """Explicit secret reveal used only by the local Copy invite button."""
    try:
        return JSONResponse(
            {"inviteText": get_rift_archive_service().invite_card()},
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    except Exception as exc:
        raise _bad_request(exc) from None


@router.delete("/connection")
async def disconnect_game() -> dict[str, Any]:
    try:
        return get_rift_archive_service().disconnect()
    except Exception as exc:
        raise _bad_request(exc) from None
