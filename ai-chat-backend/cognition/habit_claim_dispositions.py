"""Explicit disposition of this extraction's candidates; never edits old books."""
import copy
from memory_v2.day_settlement_checkpoint import _digest
from .habit_cycle_io import Pending

CONTRACT = '''这是本请求唯一一次局部修正，完整原始材料和旧栏目保持不变。
claim_cases 列出本次初提取的每条认识，review_feedback 是独立审核意见而非新事实。
返回 habit_reviews，并在顶层返回 claim_dispositions，每个原 case.id 恰好一项：
{"id":"原case.id","action":"retain|revise|withdraw|defer","claim_index":修稿claims中的1起编号,"reason":"处理原因"}。
已获支持的认识用 retain，原样保留其目标、文字和证据。失败认识可以 revise：补齐真实证据或在同目标中保留条件地收窄原认识，仍需独立复审。
确无可支持修正时用 withdraw，并写明本次不采纳原因；该项省略 claim_index，不放入修稿claims。withdraw只撤回本次候选，既不认定旧书错误，也不删除或认证旧书。
仍需处理的认识用 defer，原样保留，写明缺口，整个请求保持待处理。
所有未撤回项与修稿claims一一对应，保持原顺序；已通过项不能撤回，失败项不能静默消失或换成无关问题。
全部撤回时 habit_reviews 用 insufficient、claims=[]、sources=[]，随后还须完整情境审核确无可支持旧认识；不得以全部撤回代替该审核。
保留正确项的通过仅覆盖这些具体认识；其余旧认识保持未复核。本阶段不写书。'''


def summary(decisions):
    counts = {a: 0 for a in ('retain', 'revise', 'withdraw', 'defer')}
    for decision in decisions:
        for row in decision.get('claim_dispositions', []):
            action = row.get('action')
            if action in counts:
                counts[action] += 1
    return ({**counts, 'scope': 'this_candidate_only_old_book_unchanged'}
            if any(counts.values()) else None)


def reconcile(raw, before, after, checks, final_outcome):
    from wander_manager.flash_structured import parse_json_object
    value = parse_json_object(raw) if isinstance(raw, str) else raw
    entries = value.get('claim_dispositions') if isinstance(value, dict) else None
    ids = [e.get('id') for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    expected = {r['id'] for r in before}
    if (not isinstance(entries, list) or len(ids) != len(entries)
            or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids)) or set(ids) != expected):
        raise Pending('cycle_disposition_coverage')
    by_id = {e['id']: e for e in entries}
    retained = []
    dispositions = []
    for old in before:
        entry = by_id[old['id']]
        action = entry.get('action')
        supported = checks[old['id']]['verdict'] == 'supported'
        reason = entry.get('reason', '')
        if action not in {'retain', 'revise', 'withdraw', 'defer'}:
            raise Pending('cycle_disposition_action')
        if supported and action != 'retain':
            raise Pending('cycle_correction_changed_accepted_claim')
        if not supported and action == 'retain':
            raise Pending('cycle_disposition_unreviewed_retain')
        if action in {'withdraw', 'defer'} and (not isinstance(reason, str) or not reason.strip()):
            raise Pending('cycle_disposition_missing_reason')
        disposition = {'id': old['id'], 'statement': old['statement'], 'target': copy.deepcopy(old['target']),
                       'action': action, 'reason': reason, 'initial_review': copy.deepcopy(checks[old['id']])}
        if action == 'withdraw':
            if 'claim_index' in entry:
                raise Pending('cycle_withdraw_has_claim')
            disposition['scope'] = 'this_candidate_only_old_book_unchanged'
        else:
            index = entry.get('claim_index')
            if type(index) is not int or index != len(retained) + 1:
                raise Pending('cycle_disposition_claim_mapping')
            retained.append((old, action))
            disposition['claim_index'] = index
        dispositions.append(disposition)
    if not retained:
        if final_outcome != 'insufficient' or len(after) != 1 or after[0]['kind'] != 'no_supported_claim':
            raise Pending('cycle_all_withdrawn_requires_material_review')
        return after, dispositions
    if final_outcome != 'supported_no_change' or len(after) != len(retained):
        raise Pending('cycle_disposition_claim_mapping')
    after = copy.deepcopy(after)
    for (old, action), new in zip(retained, after):
        if action in {'retain', 'defer'}:
            if old != new:
                raise Pending('cycle_correction_changed_accepted_claim' if action == 'retain'
                              else 'cycle_deferred_claim_changed')
        elif (old['target'] != new['target'] or not (
                old['statement'] in new['statement'] or new['statement'] in old['statement'])):
            raise Pending('cycle_correction_changed_question')
        elif old['id'] != new['id']:
            new['revision_of'] = {'statement': old['statement'], 'target': old['target'],
                                  'feedback': checks[old['id']]}
            new['id'] = _digest(new)
    return after, dispositions
