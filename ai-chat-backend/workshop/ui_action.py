"""Read-time provenance for a deliberate workshop invitation."""

EVENT_TYPE = 'ui_action:visit_workshop'
CONTENT = 'AI，去你的小机手工作坊坐坐吧～带个小想法，也看看已有工单和作品。'


def invitation_fact(content='', timestamp=''):
    parts = [f'事件时间={timestamp}'] if timestamp else []
    parts.append('人类伙伴点击了工作坊快捷邀请，邀请 AI 来自己的小机手工作坊')
    if content:
        parts.append(f'界面固定文案为：{content}')
    parts.append('这是界面生成的操作，不是人类伙伴手写的私聊；后台已启动来访，所选创作方向与附话是本次请求，具体题材和表达由 AI 填写')
    return '；'.join(parts) + '。'
