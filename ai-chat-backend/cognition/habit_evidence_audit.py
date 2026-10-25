"""Opt-in citation entailment audit; no dispatch, publication or cache alias.

Full request context stays frozen with the extraction. This narrower question
asks whether a claim's actual citations prove it, not whether it may be true.
Reports asserting insufficient material still need the existing full-context
review. This prototype never selects itself in daily maintenance.
"""
import copy

VERSION = 'habit-citation-entailment-v2'
CONTRACT = '''核对每个 case 的“这组引文是否足以证明这条完整认识”。
claim 是待证明命题，target_text 是完整旧栏目，只供比较命题是否丢失条件。sources 保留本项选中消息的完整原文、说话人和时间；citations 是本项实际选择的引文。
先对照完整命题里的事实和限定，再判断这些引文是否共同推出它。说法相容、常识上合理、旧书已有这句话，都不同于来源已证明。AI 的复述保留为 AI 的说法，人类伙伴的自述由人类伙伴原话证明。
按自然语言理解原话中的转折、因果和指代，允许保留原意的等义概括；命题无须逐字出现在来源中。将本人已表达的意思与仅由他人代述的意思分清。
supported 表示每个事实、时间、情境和频率均有选中引文支持。缺少一部分依据用 insufficient；引文明确相反用 contradicted。日期可由该引文的可信时间元数据支持。完整消息帮助理解原引文；需要额外引文才能成立时指出缺口，由提取阶段修订引用。
各 case 的证据范围独立，其他 case 的来源不属于本项。给每项写一句具体核对结论：支持时说明关键对应，缺少或相反时明确指出是哪段命题。
输出 JSON {"contract":"habit-citation-entailment-v2","checks":[{"id":"原样复制对应 case.id","verdict":"supported|insufficient|contradicted","finding":"具体核对结论","basis":[{"n":1,"quote":"本项 citations 内的连续原话"}]}]}。
checks 的 id 原样取自本次 cases，每个 case 各返回一次；支持项至少一条 basis，不足项可空。无需改写认识或生成新事实。'''


def case(case_id, claim, target_text, evidence, sources):
    """Preserve complete selected messages; numbering is local to one case."""
    if not claim or claim not in target_text or not evidence:
        raise ValueError('citation_audit_claim_invalid')
    known = {r['id']: r for r in sources}
    ids = list(dict.fromkeys(r['message_id'] for r in evidence))
    if not set(ids) <= known.keys():
        raise ValueError('citation_audit_source_missing')
    n_of = {mid: n for n, mid in enumerate(ids, 1)}
    citations = []
    for ref in evidence:
        quote = ref.get('quote')
        if not isinstance(quote, str) or not quote.strip() or quote not in known[ref['message_id']]['text']:
            raise ValueError('citation_audit_quote_invalid')
        citations.append({'n': n_of[ref['message_id']], 'quote': quote})
    return {'id': case_id, 'claim': claim, 'target_text': target_text,
        'sources': [{**copy.deepcopy(known[mid]), 'n': n_of[mid]} for mid in ids],
        'citations': citations}


def assess(raw, payload):
    version = payload.get('review_contract', VERSION)
    if (version not in {'habit-citation-entailment-v1', VERSION}
            or not isinstance(raw, dict) or raw.get('contract') != version):
        raise ValueError('citation_audit_contract')
    cases = {c['id']: c for c in payload['cases']}
    checks = raw.get('checks')
    if (len(cases) != len(payload['cases']) or not isinstance(checks, list)
            or len(checks) != len(cases) or any(not isinstance(r, dict) for r in checks)):
        raise ValueError('citation_audit_coverage')
    ids = [r.get('id') for r in checks]
    if any(not isinstance(rid, str) for rid in ids) or len(set(ids)) != len(ids) or set(ids) != set(cases):
        raise ValueError('citation_audit_identity')
    decisions = []
    for check in checks:
        row = cases[check['id']]
        verdict, refs, finding = check.get('verdict'), check.get('basis'), check.get('finding')
        if (verdict not in {'supported', 'insufficient', 'contradicted'} or not isinstance(refs, list)
                or not isinstance(finding, str) or not finding.strip()):
            raise ValueError('citation_audit_verdict')
        if verdict == 'supported' and not refs:
            raise ValueError('citation_audit_support_missing')
        for ref in refs:
            if (not isinstance(ref, dict) or type(ref.get('n')) is not int
                    or not isinstance(ref.get('quote'), str) or not ref['quote'].strip()
                    or not any(c['n'] == ref['n'] and ref['quote'] in c['quote'] for c in row['citations'])):
                raise ValueError('citation_audit_evidence_outside_case')
        decisions.append(copy.deepcopy(check))
    return {'contract': version, 'checks': decisions,
        'supported_case_ids': [r['id'] for r in decisions if r['verdict'] == 'supported'],
        'coverage_complete': True, 'semantic_quality_verified': False}
