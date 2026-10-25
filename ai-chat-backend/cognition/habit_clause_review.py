"""Opt-in next review contract. Pure material/receipt functions; no dispatch.

Not selected by daily maintenance: changing an audit contract must not reopen
old paid, unknown, or held components. Semantic correctness still needs a real
source audit; these checks enforce provenance and complete review coverage.
"""
import copy
from . import habit_claim_review as legacy

VERSION = 'habit-claim-clause-review-v2'
CONTRACT = '''逐项核验既有习惯报告的具体认识和引用。完整 messages 提供上下文，targets 是待核验的旧认识，旧书不能证明自己。
每条 claim 的 evidence 才是本项选定依据；request.reference_ids 是可读背景范围。其他 claim 或背景中虽有相关事实，也不能替本项补上没选的引用。需要换引文时本报告保留 insufficient，另行修订提取结果。
review_units 是按原标点无损排版，不是语义拆句。逐段连同完整 claim、主体、上下文和前后条件核验，不将承接片段当独立断言。一个片段内的每个型号、数量、时间、动作、状态、否定和频率都须被实际所选原话支持。
完整 claim 是判断单位；条件、否定、代词承接与相邻段可由同一原话联合支持，不要求每段有独立事件。没有时间断言时，time=supported 表示未把原话升级出额外时间或完成结论，不需编造日期。
核验的是来源是否支持命题，不是是否可能相容：来源为真而本段细节仍可能不成立时，不能给 supported。计划与完成、一次经历与规律自述、发生频次与反复谈论分别保留。
每个片段分别返回 subject/time/content/scope 的 supported|unsupported|unknown，以及自己的 evidence:[{"n":来源序号,"quote":"所选证据中的连续逐字原话"}]。supported 的每段至少有一条来源引文；引文真实仍不等于蕴含成立。任何维度不足都不能批准整条报告。
insufficient 报告则用整个 baseline_targets 和本请求完整 reference_ids 核验“材料尚不足”的判断，claims=[]，checked_reference_ids 精确列出本请求序号；不把未查看当不足，也不把不足当作旧认识错误。
输出 JSON {"contract":"habit-claim-clause-review-v2","reviews":[{"id":"请求id","verdict":"supported|unsupported|insufficient","checked_reference_ids":[],"claims":[{"index":1,"units":[{"text":"review_units 中原样片段","subject":"supported|unsupported|unknown","time":"supported|unsupported|unknown","content":"supported|unsupported|unknown","scope":"supported|unsupported|unknown","evidence":[{"n":1,"quote":"逐字原话"}]}]}]}]}。
每个请求恰好一项，每个 claim 和每个 review_unit 恰好一项且原顺序完整。checked_reference_ids 对 supported_no_change 留空；对 insufficient 覆盖完整请求。审核收据不改认识、不替报告补证、不生成行动指令。
'''


def review_units(text):
    """Lossless sentence layout; commas retain conditions and their consequents."""
    units, start = [], 0
    for index, char in enumerate(text):
        if char in '。！？\n' and text[start:index + 1].strip():
            units.append(text[start:index + 1])
            start = index + 1
    if start < len(text):
        if text[start:].strip() or not units:
            units.append(text[start:])
        else:
            units[-1] += text[start:]
    return units


def material(requests, reports, evidence):
    """Preserve full legacy material; append only the clause coverage layout."""
    selected, payload = legacy.material(requests, reports, evidence)
    payload = copy.deepcopy(payload)
    payload['review_contract'] = VERSION
    for report in payload['reports']:
        if report['outcome'] == 'supported_no_change':
            for claim in report['claim_support']:
                claim['review_units'] = review_units(claim['claim'])
    return selected, payload


def assess(value, payload):
    """Fail closed on incomplete/borrowed evidence; never infer semantics."""
    if not isinstance(value, dict) or value.get('contract') != VERSION:
        raise ValueError('clause_review_contract')
    reports = {r['id']: r for r in payload['reports']}
    requests = {r['id']: r for r in payload['requests']}
    sources = {row['n']: row for row in payload['messages']}
    rows = value.get('reviews')
    if not isinstance(rows, list) or len(rows) != len(reports):
        raise ValueError('clause_review_coverage')
    if any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows):
        raise ValueError('clause_review_identity')
    by_id = {r['id']: r for r in rows}
    if len(by_id) != len(rows) or set(by_id) != set(reports):
        raise ValueError('clause_review_identity')
    approved, rejections = [], []
    for rid, report in reports.items():
        row = by_id[rid]
        issue = _report_issue(row, report, requests[rid], sources)
        if issue:
            rejections.append({'id': rid, 'code': issue})
        else:
            approved.append(rid)
    return {'approved_ids': approved, 'rejections': rejections,
            'approval_scope': 'cited_claims_only', 'semantic_quality_verified': False}


def _report_issue(row, report, request, sources):
    if row.get('verdict') not in ('supported', 'unsupported', 'insufficient'):
        return 'review_verdict_invalid'
    if report['outcome'] == 'insufficient':
        checked = row.get('checked_reference_ids')
        if (not isinstance(checked, list) or any(type(n) is not int for n in checked)
                or len(set(checked)) != len(checked)
                or set(checked) != set(request['reference_ids'])):
            return 'insufficient_source_coverage'
        return None if row['verdict'] == 'insufficient' and row.get('claims') == [] else 'insufficient_not_confirmed'
    if row['verdict'] != 'supported':
        return 'claim_not_supported'
    if row.get('checked_reference_ids') != []:
        return 'unexpected_background_support'
    checks, claims = row.get('claims'), report.get('claim_support', [])
    if not isinstance(checks, list) or len(checks) != len(claims) or not claims:
        return 'claim_coverage'
    for index, (check, claim) in enumerate(zip(checks, claims), 1):
        if not isinstance(check, dict) or type(check.get('index')) is not int or check['index'] != index:
            return 'claim_identity'
        units = check.get('units')
        expected = review_units(claim['claim'])
        if (not isinstance(units, list) or len(units) != len(expected)
                or any(not isinstance(u, dict) for u in units)
                or [u.get('text') for u in units] != expected):
            return 'clause_coverage'
        for unit in units:
            if any(unit.get(k) != 'supported' for k in ('subject', 'time', 'content', 'scope')):
                return 'clause_not_supported'
            refs = unit.get('evidence')
            if not isinstance(refs, list) or not refs:
                return 'clause_evidence_missing'
            seen = set()
            for ref in refs:
                if not isinstance(ref, dict) or type(ref.get('n')) is not int:
                    return 'clause_reference_invalid'
                n, quote = ref['n'], ref.get('quote')
                if (n not in request['reference_ids'] or n not in sources
                        or not isinstance(quote, str) or not quote.strip()
                        or quote not in sources[n]['text']
                        or not any(e['n'] == n and quote in e['quote'] for e in claim['evidence'])):
                    return 'clause_evidence_outside_selected_claim'
                if (n, quote) in seen:
                    return 'clause_evidence_duplicate'
                seen.add((n, quote))
    return None
