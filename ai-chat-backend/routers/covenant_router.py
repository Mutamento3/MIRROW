"""Conversation covenant runtime API."""

from fastapi import APIRouter
from pydantic import BaseModel

from conversation_covenant import get_conversation_covenant_service

router = APIRouter(prefix="/api/conversation-covenant", tags=["conversation-covenant"])


class CovenantActivationRequest(BaseModel):
    message: str
    session_id: str


@router.on_event("startup")
async def covenant_startup() -> None:
    await get_conversation_covenant_service().start()


@router.on_event("shutdown")
async def covenant_shutdown() -> None:
    await get_conversation_covenant_service().stop()


@router.get("/status")
async def covenant_status():
    return await get_conversation_covenant_service().status()


@router.post("/activate")
async def covenant_activate(request: CovenantActivationRequest):
    activated = await get_conversation_covenant_service().observe_message(
        request.message, request.session_id
    )
    status = await get_conversation_covenant_service().status()
    return {"activated": activated, **status}


@router.post("/cancel")
async def covenant_cancel():
    cancelled = await get_conversation_covenant_service().cancel()
    return {"cancelled": cancelled, **(await get_conversation_covenant_service().status())}
