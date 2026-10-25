"""Choose complete source-backed impressions inside one summary budget."""
from time_utils import parse_ts


def select_summary_items(items, *, budget, label, recollection_ids=()):
    """Keep chronological output; selection never changes stored memories.

    A small fragment receives no minimum allocation of its own. Prefer the
    latest outcome, actual dialogue and salient impressions; retain complete
    summaries rather than cutting away their endings to fit every candidate.
    """
    budget = max(0, int(budget))
    ordered = sorted(items, key=lambda i: (parse_ts(i.get('start_ts') or ''), int(i.get('order') or 0)))
    blocks = {i['order']: label(i) + '\n' + str(i['summary']).strip() for i in ordered}
    if sum(len(blocks[i['order']]) + 2 for i in ordered) <= budget:
        return ordered, blocks, ''
    if not ordered:
        return [], blocks, ''
    # The note is factual coverage metadata, not a directive to AI.
    note = '[昨日记忆只呈现部分梗概，其余经历仍保存在记忆库中。]'
    remaining = max(0, budget - len(note) - 2)
    latest = ordered[-1]['order']
    recollections = set(recollection_ids)
    ranked = sorted(ordered, key=lambda i: (
        i['order'] == latest,
        bool(recollections.intersection(i.get('message_ids') or [])),
        int(i.get('user_source_count') or 0) > 0,
        float(i.get('vividness') or 0),
        parse_ts(i.get('end_ts') or ''),
    ), reverse=True)
    selected = set()
    for item in ranked:
        cost = len(blocks[item['order']]) + 2
        if cost <= remaining:
            selected.add(item['order'])
            remaining -= cost
    return [i for i in ordered if i['order'] in selected], blocks, note if selected else ''
