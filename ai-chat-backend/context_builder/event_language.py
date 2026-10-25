"""Deterministic factual language; trigger and actor are separate facts."""


def action_origin(action: str, source: str = '') -> str:
    if source == 'ui_action':
        return f'人类伙伴通过快捷按钮邀请 AI {action}；本次行动者是 AI。'
    if source in {'autonomy', 'autonomous'}:
        return f'AI 在漫想中自主选择{action}；本次行动者是 AI。'
    return f'本次行动者是 AI，行动是{action}；具体触发方式未记录。'


def encounter_status(status: str, turns: int) -> str:
    label = {'completed':'已正常结束', 'interrupted':'已中断',
             'rejected':'未能开始', 'running':'仍在进行中'}.get(status, '结束状态尚未确认')
    return f'本次会客{label}，记录了 {turns} 回合。'
