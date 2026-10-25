"""AI-card projection from one confirmed read node and optional link evidence."""
from __future__ import annotations

from contextlib import closing
from uuid import uuid4

from .links import parse_share_link
from .store import XhsPostStore


def k_card_from_node(store: XhsPostStore, node_id: str, *, note: str = "",
                     comment_draft: str = "", require_invite_link: bool = True) -> dict:
    with closing(store._connect()) as conn:
        row, payload = store._node(conn, node_id)
    bound = store.link_for_node(node_id)
    link = None
    if bound.get("source_url"):
        try:
            link = parse_share_link(str(bound["source_url"]))
            link["link_status"] = str(bound.get("link_status") or "share_unverified")
        except ValueError:
            pass
    if not link:
        try:
            link = parse_share_link(str(payload.get("post_url") or ""))
        except ValueError:
            pass
    if comment_draft.strip() and not link and require_invite_link:
        raise ValueError("评论邀请需要可靠分享链接；当前帖子尚无可核实链接")
    return {
        "id": "xhs-k-" + uuid4().hex, "kind": "xhs",
        "title": str(payload.get("title") or "")[:160],
        "body": (note or row["reflection"] or payload.get("content_summary") or "")[:600],
        "link": link["url"] if link else "", "post_key": "xhs-node:" + node_id,
        "link_status": link["link_status"] if link else "unavailable",
        "source_status": "k_read", "node_id": node_id,
        "comment_draft": comment_draft.strip()[:1000] if link else "",
    }
