"""AI's local post shelf actions over verified Wander read nodes only."""
from __future__ import annotations

from uuid import uuid4

from .base_tool import BaseTool, ToolResult, ToolStatus


class XhsPostsTool(BaseTool):
    name = "xhs_posts"
    description = "查看 AI 实际读过的小红书帖子；在 MIRROW 本地留帖、移出、分享或邀请人类伙伴手动评论。不会点赞、平台收藏或自动评论。"
    flash_description = "AI 的小红书本地留帖：查实际读过的帖子、留下或移出、分享已读帖子卡片、邀请人类伙伴手动评论"
    pro_guide = ("查最近真实读过的帖子或 AI 本地留帖；凭返回的帖子标识与理由保存、移出本地留帖，平台不变。"
                 "分享已读帖子可寄卡片；邀请人类伙伴评论时附评论草稿，供她复制后亲自评论。"
                 "没有可靠原帖链接时卡片明确不可直达，邀请会失败。")
    ui_only_actions = frozenset({"share", "invite"})
    parameters_schema = {
        "type": "object", "properties": {
            "action": {"type": "string", "enum": ["recent", "list", "save", "unsave", "share", "invite"]},
            "node_id": {"type": "string"}, "query": {"type": "string"},
            "reason": {"type": "string", "maxLength": 300},
            "comment_draft": {"type": "string", "maxLength": 1000},
        }, "required": ["action"],
    }

    def get_result_delivery(self, **kwargs) -> str:
        result = kwargs.get("_result")
        if kwargs.get("_execution_failed") or (result is not None and result.status != ToolStatus.SUCCESS):
            return "model"
        return super().get_result_delivery(**kwargs)

    async def execute(self, action: str, node_id: str = "", query: str = "",
                      reason: str = "", comment_draft: str = "", **kwargs) -> ToolResult:
        from xhs_posts.store import XhsPostStore
        store = XhsPostStore()
        try:
            if action == "recent":
                from experience_recall.service import records
                items = [item for item in records({"xiaohongshu"}) if item.id.startswith("xhs-node:")]
                items = [item for item in items if not query.strip() or query.strip() in (item.title + item.fact + item.feeling)][:15]
                content = "\n".join(f"node_id={item.id.removeprefix('xhs-node:')} · {item.title} · {item.happened_at[:16]}" for item in items)
                return ToolResult(ToolStatus.SUCCESS, content or "没有匹配的真实读帖记录")
            if action == "list":
                items = store.list_posts(query=query, limit=30)
                content = "\n".join(f"node_id={item['node_id']} · {item['title'] or '无标题'} · 理由：{item['last_reason'] or '未记录'} · 链接：{'有分享链接' if item['source_url'] else '无可靠直达链接'} · 人类伙伴自述评论：{item['comment_self_reports']} 次（非平台核验）" for item in items)
                return ToolResult(ToolStatus.SUCCESS, content or "AI 的 MIRROW 留帖中没有匹配项")
            if action in {"save", "unsave"}:
                result = store.save_from_node(
                    node_id, reason=reason, actor="k_tool", save=action == "save",
                    idempotency_key=f"k-tool:{kwargs.get('_message_id') or uuid4().hex}:{action}:{node_id}",
                )
                verb = "已留在 MIRROW" if action == "save" else "已从 MIRROW 留帖移出"
                return ToolResult(ToolStatus.SUCCESS, f"{verb}；小红书平台未作任何操作。",
                                  extra_data={"xhs_post_action": {"action_id": result["action_id"], "post_key": result["post_key"], "changed": bool(result["changed"])}})
            if action in {"share", "invite"}:
                # The action ledger is not advanced here: a share only exists
                # after the owning assistant message/tool attachment persists.
                if action == "invite" and not comment_draft.strip():
                    return ToolResult(ToolStatus.ERROR, "", error="评论邀请需要 AI 写的评论草稿")
                from xhs_posts.cards import k_card_from_node
                card = k_card_from_node(store, node_id, note=reason,
                                        comment_draft=comment_draft if action == "invite" else "")
                content = "帖子卡片已挂载；仅有当前节点可见内容" + ("，等待人类伙伴自己决定是否评论" if action == "invite" else "")
                return ToolResult(ToolStatus.SUCCESS, content, extra_data={"xhs_post_card": card}, delivery="ui_only")
            return ToolResult(ToolStatus.ERROR, "", error="不支持的小红书留帖动作")
        except ValueError as exc:
            return ToolResult(ToolStatus.ERROR, "", error=str(exc))
        except Exception as exc:
            return ToolResult(ToolStatus.ERROR, "", error=f"留帖操作未完成：{type(exc).__name__}")
