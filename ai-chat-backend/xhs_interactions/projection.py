"""Immutable legacy Memory sources and explicit archived receipt details."""
import json


def project(value):
    if not isinstance(value, dict):
        return ''
    from .service import experience
    parts = []
    direct = experience(value)
    if direct:
        parts.append(direct)
    if isinstance(value.get('items'), list):
        parts.append('小红书通知发现索引（不表示AI已经读到原帖或评论）：\n' +
                     json.dumps(value['items'], ensure_ascii=False))
    if isinstance(value.get('materials'), list) and value['materials']:
        label = ('AI本次实际收到的外部来源材料' if value.get('k_received') is True
                 or value.get('status') == 'completed' else '专用机已取得的外部来源材料；AI接收尚未确认')
        parts.append(label + '：\n' + json.dumps(value['materials'], ensure_ascii=False))
    for row in value.get('xhs_interactions') or []:
        if not isinstance(row, dict):
            continue
        if row.get('status') == 'read_without_reply':
            parts.append('AI已读后决定不回复；互动=' + str(row.get('interaction_id') or '') +
                         '；AI当时的缘由：' + str(row.get('reason') or ''))
        else:
            fact = experience(row)
            if fact:
                parts.append(fact)
    if isinstance(value.get('reflection'), str) and value['reflection'].strip():
        parts.append('AI当时原述感想：' + value['reflection'])
    if not parts:
        parts.append('小红书互动执行状态：' + str(value.get('status') or 'unknown'))
    if value.get('attention_stage'):
        parts.append('领域阶段：' + str(value['attention_stage']) + '；未知模型调用或提交不自动重放。')
    return '\n'.join(parts)
