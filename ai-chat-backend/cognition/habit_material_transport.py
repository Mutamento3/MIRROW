"""Lossless sharing of identical frequency tables in one model request.

This is a transport projection only. The frozen payload and report identities
remain authoritative; no source, frequency record or counter is filtered.
"""
import json
from collections import Counter

JOIN = ('\n习惯材料无损索引：habit_frequency_sets 保存完整频次材料表。'
        '请求的 frequency_set_id 对应该表的同名键，其完整内容就是该请求的 frequency_evidence。'
        '多个请求引用同一表表示共享同一份材料，不表示多次行为；其余字段及引用编号保持原义。')


def compact(payload):
    requests = payload.get('habit_review_requests', [])
    if not isinstance(requests, list):
        return payload, False
    def key(row):
        value = row.get('frequency_evidence') if isinstance(row, dict) else None
        return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, list) and value else None
    keys = [key(row) for row in requests]
    counts = Counter(k for k in keys if k is not None)
    if not any(n > 1 for n in counts.values()):
        return payload, False
    if 'habit_frequency_sets' in payload or any('frequency_set_id' in r for r in requests):
        raise ValueError('habit_frequency_transport_collision')
    table, indexes, projected = {}, {}, []
    for row, identity in zip(requests, keys):
        if identity is None or counts[identity] < 2:
            projected.append(row)
            continue
        if identity not in indexes:
            index = str(len(indexes) + 1)
            indexes[identity] = index
            table[index] = row['frequency_evidence']
        projected.append({**{k:v for k,v in row.items() if k != 'frequency_evidence'},
                          'frequency_set_id': indexes[identity]})
    return {**payload, 'habit_review_requests': projected, 'habit_frequency_sets': table}, True


def expand(payload):
    if 'habit_frequency_sets' not in payload:
        return payload
    requests = []
    for row in payload['habit_review_requests']:
        if 'frequency_set_id' not in row:
            requests.append(row)
        else:
            requests.append({**{k:v for k,v in row.items() if k != 'frequency_set_id'},
                             'frequency_evidence': payload['habit_frequency_sets'][row['frequency_set_id']]})
    return {**{k:v for k,v in payload.items() if k != 'habit_frequency_sets'},
            'habit_review_requests': requests}
