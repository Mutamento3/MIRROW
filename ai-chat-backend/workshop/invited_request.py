"""Selected creative invitations are requests, separate from autonomous ideas."""
from .creative_templates import find


def starts_new_work(invocation):
    invitation = invocation or {}
    return (invitation.get('source') == 'ui_action' and
            not invitation.get('focus_job_id') and not invitation.get('job_id') and
            find(invitation.get('template_id')) is not None and
            invitation['template_id'] != 'existing_upgrade')


async def decide_first(decide, messages, visual, invocation):
    if not starts_new_work(invocation):
        return await decide(messages, visual)
    contract = ('【人类伙伴本次快捷邀请的执行合同】本次已经进入工作坊来访。'
                '她选定的创作方向和附话是这次明确请求；先用 create 提交对应的小作品需求与验收清单。'
                '题材、风格和赠言由你结合实际上下文填写。已有旧工单仍保留，但这次新作品不追加到旧单。'
                '本轮 action=create；none 或处理旧单不完成本次邀请。提交后短讨论确认方案；静态作品走轻量交付，交互作品保留质检。')
    result = await decide(messages, visual, execution_contract=contract)
    if (result.get('action') or {}).get('action') != 'create':
        # One contract repair; no fabricated AI action or endless retry loop.
        result = await decide(messages, visual, execution_contract=contract +
            '\n上一轮没有提交本次请求的新作品。请输出 action=create，写出你自己的需求与 A1–A3 验收清单。')
    if (result.get('action') or {}).get('action') != 'create':
        raise RuntimeError('invited_request_not_submitted')
    return result
