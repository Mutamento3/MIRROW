"""Small history facts; source bodies remain in the interaction authority.

This is a presentation/capture contract, not a replacement for the immutable
legacy projection used by already indexed Memory V2 sources.
"""
from collections import Counter
from copy import deepcopy
import re

from context_builder.text_utils import _redact_tool_fact

LABELS = {
    'published': '平台已核验回复', 'previewed': '仅预览回复',
    'submitted_unverified': '回复提交未核验', 'submitting': '回复提交结果未知',
    'preparing': '回复准备未确认', 'blocked': '回复准备受阻',
    'draft': '回复草稿', 'read_without_reply': '已读后离开',
}


def capture(value):
    """Freeze counts, original feeling and machine-only domain references."""
    if not isinstance(value, dict):
        value = {}
    if isinstance(value.get('interaction_snapshot'), dict):
        value = value['interaction_snapshot']
    if value.get('context_version') == 2:
        return deepcopy(value)
    counts = Counter()
    interaction_ids, reply_ids, dates = set(), set(), []
    def source(item):
        if not isinstance(item, dict): return
        if item.get('id'): interaction_ids.add(str(item['id']))
        dates.extend(str(item.get(k) or '') for k in ('read_at', 'created_at'))
    sources = value.get('materials') or []
    for item in sources: source(item)
    if sources:
        counts['read' if value.get('status') == 'completed' or value.get('k_received') is True
               else 'obtained'] = len(sources)
    if isinstance(value.get('interaction'), dict):
        source(value['interaction'])
        counts['read'] = 1
    if isinstance(value.get('items'), list):
        counts['discovered'] = len(value['items'])
    def outcome(row):
        if not isinstance(row, dict): return
        receipt = row.get('xhs_reply') or {}
        state = receipt.get('state') or row.get('status') or 'unknown'
        counts[state if state in LABELS else 'unconfirmed'] += 1
        if row.get('interaction_id'): interaction_ids.add(str(row['interaction_id']))
        if receipt.get('interaction_id'): interaction_ids.add(str(receipt['interaction_id']))
        if receipt.get('id'): reply_ids.add(str(receipt['id']))
        dates.extend(str(receipt.get(k) or '') for k in ('updated_at', 'created_at'))
    for row in value.get('xhs_interactions') or []: outcome(row)
    if isinstance(value.get('xhs_reply'), dict): outcome(value)
    dates.append(str(value.get('observed_at') or ''))
    date = max((stamp[:10] for stamp in dates if re.match(r'^\d{4}-\d{2}-\d{2}', stamp)), default='')
    refs = {'domain': 'xhs_interactions', 'interaction_ids': sorted(interaction_ids),
            'reply_ids': sorted(reply_ids)}
    if value.get('batch_id'): refs['batch_id'] = str(value['batch_id'])
    return {'context_version': 2, 'status': value.get('status') or 'unknown',
            'counts': dict(counts), 'reflection': _redact_tool_fact(value.get('reflection') or ''),
            'date': date, 'refs': refs}


def brief(value, *, timestamp='', include_feeling=True, reference=True):
    view = capture(value)
    counts = view['counts']
    parts = []
    for key, label in [('discovered', '发现通知（原文未读）'), ('obtained', '已取得原文（AI接收未确认）'),
                       ('read', '已读互动'), *LABELS.items(), ('unconfirmed', '处理未确认')]:
        if counts.get(key): parts.append(f'{label}{counts[key]}条')
    if not parts:
        parts.append('无新互动' if view['status'] == 'no_pending_interactions' else '处理尚未完成')
    lines = ['小红书互动：' + '；'.join(parts) + '。']
    if include_feeling and view.get('reflection') and not view.get('reflection_is_retained'):
        lines.append('AI当时的心绪（原述）：' + view['reflection'])
    if reference:
        date = str(timestamp or view.get('date') or '')[:10]
        lines.append('原文见' + (date if re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) else '当次') + '小红书互动记录。')
    return '\n'.join(lines)


def material(value):
    """New event cards carry this small snapshot, never a copy of post bodies."""
    view = capture(value)
    return {'kind': 'xhs_interactions', **view,
            **({'account_source':'k_interaction_decision'} if view.get('reflection') else {}),
            'description': brief(view, include_feeling=False, reference=False)}
