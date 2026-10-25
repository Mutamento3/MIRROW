"""Local Xiaohongshu post shelf. These endpoints never interact with XHS."""
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from xhs_posts.links import parse_share_link
from xhs_posts.store import XhsPostStore

router = APIRouter(prefix="/api/xhs-posts", tags=["xhs-posts"])


class PreviewRequest(BaseModel):
    text: str


@router.post("/resolve-preview")
def resolve_preview(data: PreviewRequest):
    try:
        return parse_share_link(data.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/saved")
def saved_posts(query: str = "", saved_only: bool = True):
    return {"items": XhsPostStore().list_posts(query=query, saved_only=saved_only)}


class RemoveRequest(BaseModel):
    idempotency_key: str = ""


class BindLinkRequest(BaseModel):
    text: str
    confirmed_same_post: bool = False


@router.post("/saved/{node_id}/bind-link")
def bind_saved_link(node_id: str, data: BindLinkRequest):
    try:
        return XhsPostStore().bind_confirmed_link(
            node_id, data.text, confirmed_same_post=data.confirmed_same_post,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


class CommentReportRequest(BaseModel):
    session_id: str
    message_id: str


@router.get("/comment-report/{message_id}")
def comment_report(message_id: str):
    report = XhsPostStore().comment_report(message_id)
    return {"self_reported": bool(report), "reported_at": report.get("created_at", "")}


@router.post("/comment-report")
def record_comment_report(data: CommentReportRequest):
    from event_chronicle import get_global_chronicle
    chronicle = get_global_chronicle()
    message = chronicle.get_message_by_msg_id(data.message_id) if chronicle else None
    if not message or message.get("session_id") != data.session_id or message.get("role") != "assistant":
        raise HTTPException(status_code=404, detail="原评论邀请消息不存在")
    try:
        calls = json.loads(message.get("tool_calls") or "[]")
    except (TypeError, ValueError):
        calls = []
    cards = [call.get("extra_data", {}).get("xhs_post_card") for call in calls
             if isinstance(call, dict) and isinstance(call.get("extra_data"), dict)]
    cards = [card for card in cards if isinstance(card, dict) and card.get("comment_draft") and card.get("node_id")]
    if len(cards) != 1:
        raise HTTPException(status_code=422, detail="这条消息没有可确认的评论邀请卡片")
    try:
        report = XhsPostStore().record_comment_self_report(data.message_id, str(cards[0]["node_id"]))
        return {"self_reported": True, "reported_at": report["created_at"], "source": "human_self_report"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/saved/{node_id}/remove")
def remove_saved_post(node_id: str, data: RemoveRequest):
    try:
        action = XhsPostStore().save_from_node(
            node_id, actor="human", save=False,
            idempotency_key=data.idempotency_key or "human-remove:" + uuid4().hex,
        )
        return {"status": "completed", "action_id": action["action_id"], "changed": bool(action["changed"])}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
