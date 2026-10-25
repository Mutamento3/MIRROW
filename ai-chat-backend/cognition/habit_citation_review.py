"""Citation codec for the explicit gate; persistence stays with claim_review."""
from . import books, habit_evidence_audit as audit
from .habit_claim_support import validate, references
from .topics import sections


def material(requests, reports, evidence):
    known = {r['id']: r for r in requests}
    snapshot = {d: books.catalog(d) for d in books.DOMAINS}
    cases = []
    for ri, report in enumerate(reports, 1):
        if report['outcome'] != 'supported_no_change':
            raise ValueError('citation_review_requires_affirmative_report')
        request = known[report['id']]
        claims = validate(report.get('claim_support'), report['targets'], request, evidence, snapshot)
        if claims is None or references(claims) != set(report['reference_ids']):
            raise ValueError('citation_review_invalid_report')
        for ci, claim in enumerate(claims, 1):
            target = report['targets'][claim['target_index'] - 1]
            entry = next(e for e in snapshot[target['domain']]
                         if e['entry_id'] == target['entry_id'] and e['revision'] == target['revision'])
            body = dict(sections(entry['body']))[target['section']]
            row = audit.case(f'r{ri}c{ci}', claim['claim'], body, claim['evidence'], evidence)
            # Current book identity participates in cache identity, never evidence.
            row['target'] = {**target, 'subject_id': request['subject_id']}
            cases.append(row)
    return {'review_contract': audit.VERSION, 'cases': cases}


def assess(raw, reports, payload):
    from wander_manager.flash_structured import parse_json_object
    value = parse_json_object(raw) if isinstance(raw, str) else raw
    checked = audit.assess(value, payload)
    by_id = {r['id']: r for r in checked['checks']}
    expected = {f'r{ri}c{ci}' for ri, r in enumerate(reports, 1)
                for ci in range(1, len(r.get('claim_support', [])) + 1)}
    if not expected or set(by_id) != expected:
        raise ValueError('citation_review_report_coverage')
    decisions, approved = [], []
    for ri, report in enumerate(reports, 1):
        claims = [by_id[f'r{ri}c{ci}'] for ci in range(1, len(report['claim_support']) + 1)]
        accepted = all(c['verdict'] == 'supported' for c in claims)
        if accepted:
            approved.append(report['id'])
        decisions.append({'id': report['id'], 'approved': accepted,
                          'verdict': ('supported' if accepted else 'unsupported'
                                      if any(c['verdict'] == 'contradicted' for c in claims) else 'insufficient'),
                          'reason_code': 'supported' if accepted else 'citation_not_entailed',
                          'contract': audit.VERSION, 'claims': claims})
    return {'approved_ids': approved, 'decisions': decisions}
