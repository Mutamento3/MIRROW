"""Shared approval coverage protocol, independent of cognition semantics."""
import json
from . import books


def parse(raw, count):
    if isinstance(raw, dict) and 'content' in raw:
        raw = raw['content']
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0]
        raw = json.loads(raw)
    if (not isinstance(raw, dict) or not isinstance(raw.get('approved_indices'), list)
            or not isinstance(raw.get('rejections'), list)):
        raise books.BookError('认知复核结构无效')
    approved, rejected = raw['approved_indices'], []
    for item in raw['rejections']:
        if not isinstance(item, dict) or not isinstance(item.get('reason'), str) or not item['reason'].strip():
            raise books.BookError('认知复核拒绝项无效')
        rejected.append(item.get('index'))
    indices = approved + rejected
    if (any(type(i) is not int for i in indices) or len(set(indices)) != len(indices)
            or set(indices) != set(range(count))):
        raise books.BookError('认知复核未完整覆盖候选')
    return raw, set(approved), rejected
