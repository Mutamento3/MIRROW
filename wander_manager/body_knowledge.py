"""Read-only public body facts, independent of runtime state and tool schemas.

This describes published components, not a promise that a host enabled them.
It never reads private documentation, initializes devices or writes cognition.
"""
import hashlib
import json

from .capability_catalog import get_capability_records

BODY_KNOWLEDGE_VERSION = 'public-body-knowledge-v1'


def get_body_knowledge() -> dict:
    records, event_names = [], []
    for item in get_capability_records():
        if item.get('scope') == 'tool':
            continue
        event_names.extend(event['display_name'] for event in item.get('events', ()))
        records.append({key: item[key] for key in
                        ('domain', 'capability', 'state', 'details', 'source', 'freshness', 'limit')})
    lines = ['[公开身体 · 结构与能力边界]',
             '来源：本仓库能力目录。模块存在不代表当前宿主已经接入；设备可用性与行动结果以真实观测、执行回执为准。']
    lines.extend(f"{item['domain']}｜{item['capability']}。{item['details']} 边界：{item['limit']}" for item in records)
    if event_names:
        lines.append('目录中的可选活动（不等于已启用）：' + '、'.join(event_names) + '。')
    payload = {'version': BODY_KNOWLEDGE_VERSION, 'records': records,
               'event_names': event_names, 'overview': '\n'.join(lines)}
    payload['revision'] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload


def body_overview() -> str:
    return get_body_knowledge()['overview']
