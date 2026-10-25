"""Bind an unchanged-cognition claim to exact book text and source excerpts.

This validates provenance, not semantic entailment. The existing independent
fact review still decides whether those excerpts actually support the claim.
"""
from . import books
from .topics import sections

VERSION = 'habit-claim-support-v1'
GUIDE = '''
support_contract=habit-claim-support-v1 的请求采用逐认识证据合同。
supported_no_change 除 targets/reference_ids 外返回 claim_support:[{
"target_index":1,"claim":"对应 targets 中该书页栏目的逐字认识片段",
"evidence":[{"n":1,"quote":"该 messages 来源中的逐字证据"}]}]。
target_index 从1开始，引用本报告的 targets；每个 target 至少有一条具体认识。
每条认识分别引用真正支持它的原话，保留主体、否定、时间与条件，不能用同领域无关原话或旧书自身循环证明。
reference_ids 恰好为这些证据的去重序号合集。本次覆盖仅为 claim_support 的具体认识，未列部分仍未复核，不把一次批准解释为整页所有事实获支持。
没有材料支持目标里的具体认识时返回 insufficient，不勉强填写 claim_support。
'''
REVIEW_GUIDE = '''
对逐认识合同逐项核对 claim_support：claim 是否忠实选自对应旧认识，evidence 是否确实支持该具体内容、主体、否定方向与适用时间，而不仅是词语相关。
引用合法但不支持认识时 approved=false；没有新增材料支持的旧认识保持原状，不因为未复核而认定旧事实错误。批准范围仅为列出的具体认识。
'''


def validate(value, bound, request, evidence, snapshot=None, *, indexed=False, issues=None):
    def reject(code, claim_index=None, evidence_index=None):
        if issues is not None:
            issues.append({'code': code,
                **({'claim_index': claim_index} if claim_index is not None else {}),
                **({'evidence_index': evidence_index} if evidence_index is not None else {})})
        return None
    if not isinstance(value, list) or not value or len(value) > 32:
        return reject('claim_list_invalid')
    available = {row['id']: row for row in evidence}
    allowed = set(request['reference_ids'])
    current = snapshot if snapshot is not None else {d: books.catalog(d) for d in books.DOMAINS}
    result, seen, covered = [], set(), set()
    for claim_index,row in enumerate(value):
        if not isinstance(row, dict):
            return reject('claim_row_invalid',claim_index)
        index, claim, refs = row.get('target_index'), row.get('claim'), row.get('evidence')
        if (type(index) is not int or not 1 <= index <= len(bound)
                or not isinstance(claim, str) or not claim.strip()
                or not isinstance(refs, list) or not refs):
            return reject('claim_fields_invalid',claim_index)
        target = bound[index - 1]
        entry = next((e for e in current.get(target['domain'], [])
                      if e.get('entry_id') == target['entry_id']
                      and e.get('revision') == target['revision']
                      and e.get('subject_id') == request['subject_id']), None)
        body = dict(sections(entry['body'])).get(target['section'], '') if entry else ''
        if claim not in body:
            return reject('claim_not_in_target',claim_index)
        if (index,claim) in seen:
            return reject('claim_duplicate',claim_index)
        normalized, used = [], set()
        for evidence_index,ref in enumerate(refs):
            if not isinstance(ref, dict):
                return reject('claim_evidence_invalid',claim_index,evidence_index)
            if indexed:
                n = ref.get('n')
                if type(n) is not int or not 1 <= n <= len(evidence):
                    return reject('claim_reference_range',claim_index,evidence_index)
                mid = evidence[n - 1]['id']
            else:
                mid = ref.get('message_id')
            quote = ref.get('quote')
            if not isinstance(mid, str) or mid not in allowed or mid not in available:
                return reject('claim_reference_scope',claim_index,evidence_index)
            if not isinstance(quote,str) or not quote.strip() or quote not in available[mid]['text']:
                return reject('claim_quote_not_in_source',claim_index,evidence_index)
            if (mid,quote) in used:
                return reject('claim_evidence_duplicate',claim_index,evidence_index)
            used.add((mid, quote))
            normalized.append({'message_id': mid, 'quote': quote})
        seen.add((index, claim)); covered.add(index)
        result.append({'target_index': index, 'claim': claim, 'evidence': normalized})
    return result if covered == set(range(1, len(bound) + 1)) else reject('claim_target_coverage')


def references(claims):
    return {ref['message_id'] for claim in claims for ref in claim['evidence']}


def model_report(report, n_of):
    result = {**report, 'reference_ids': [n_of[mid] for mid in report['reference_ids']]}
    if 'claim_support' in report:
        result['claim_support'] = [{**claim, 'evidence': [
            {'n': n_of[ref['message_id']], 'quote': ref['quote']} for ref in claim['evidence']]}
            for claim in report['claim_support']]
    return result
