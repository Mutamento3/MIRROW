"""Frozen replay text, independent of the mutable live UI/context formatter.

These bytes match the projection used by the completed 2026-10-03 repair and
the resumable 10-04/10-05 checkpoints. Altering them requires an explicit
source migration, not a change to workshop.ui_action.invitation_fact.
"""


def invitation_fact(content: str) -> str:
    parts = ['人类伙伴点击了工作坊快捷邀请，邀请 AI 来自己的小机手工作坊']
    if content:
        parts.append(f'界面固定文案为：{content}')
    parts.append('这是界面生成的操作，不是人类伙伴手写的私聊；后台已启动来访，所选创作方向与附话是本次请求，具体题材和表达由 AI 填写')
    return '；'.join(parts) + '。'
