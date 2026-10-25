"""Due behavioral material reviews inside the existing factual day-end lane.

Receipts record review coverage, not accepted habits. Calendar checkpoints
schedule examination; they never promote a pattern or erase a book entry.
"""
from datetime import date, timedelta
import json

from . import books, maintenance
from .maintenance_sources import merge_evidence, parse_response
from .topics import sections
from .other_temporal import companion_titles
from .habit_frequency_evidence import select as frequency_select, model_rows, GUIDE as FREQUENCY_GUIDE

OUTCOMES = {'updated', 'supported_no_change', 'insufficient', 'conflicting'}
VERSION = 'habit-review-v3'
CLAIM_START_DATE = '2026-10-17'


def contract_version(source_date):
    if source_date >= '2026-10-18':
        return 'habit-review-v6-baseline-sources'
    return 'habit-review-v5-claim-gate' if source_date >= CLAIM_START_DATE else VERSION


def extraction_contract(requests):
    from .habit_claim_support import GUIDE
    from .habit_local_contract import enabled, guide, SPAN_VERSION
    LOCAL_GUIDE = guide(requests)
    if requests and all(enabled(r) for r in requests):
        return LOCAL_GUIDE + '\n' + FREQUENCY_GUIDE
    return EXTRACTION + (GUIDE if any(r.get('support_contract') for r in requests) else '') + (
        ('\n带 output_contract 的请求按各自版本采用以下合同：\n'
         if any(r.get('output_contract') == SPAN_VERSION for r in requests) else
         '\n带 output_contract=habit-local-selection-v1 的请求采用以下局部编号合同：\n') + LOCAL_GUIDE
        if any(enabled(r) for r in requests) else '')


def review_contract(requests):
    from .habit_claim_support import REVIEW_GUIDE
    from .habit_local_contract import enabled
    return REVIEW + (REVIEW_GUIDE if any(r.get('support_contract') for r in requests) else '') + (
        '\n未批准的项附 reason_code（unsupported、insufficient、scope_mismatch）及 claim_indexes、source_numbers 定位；未知原因用 unknown。'
        if any(enabled(r) for r in requests) else '')
EXTRACTION = '''habit_review_requests 是到期的跨日材料复核，不是习惯结论。每个请求对应一个材料领域，先按具体行为、情境和来源划分问题，不把宽领域计数当成一种习惯。
分别看频率、观察跨度和条件：长期低频与近期高频都可记录；没有提起不等于没发生，同一次经历多次讨论不等于多次发生。观测起始日期是材料积累时间，不是行为开始时间。
7/28/90 是检查周期，不是认识晋级线。明确规律自述可直接记录其自述性质；观察规律保留真实范围、反例和变化，材料不足也留下复核结果。
正常 proposals 仍经原事实审核。另输出 habit_reviews，每个请求恰好一项 {"id":"请求id","outcome":"updated|supported_no_change|insufficient|conflicting","reference_ids":[支持本次判断的 messages 序号],"targets":[{"domain":"world|other|self","entry_id":"现行书页id","revision":"现行版本","section":"该具体认识的二级标题，无栏目时为空"}]}。supported_no_change 必须明确是哪一个既有具体认识得到材料支持，并列出 targets；宽领域出现材料不是原认识得到复核。没有对应旧认识且尚不足以形成新认识时用 insufficient。updated 表示本次提出修订，不代表已入书；insufficient 可以没有引用。'''
REVIEW = '''另核对 habit_reviews 与请求范围、原始材料及现行书页是否一致，输出 habit_review_approvals:[{"id":"请求id","approved":true或false}]，每个请求恰好一项。supported_no_change 逐项核对 targets 的具体旧认识是否确由所引材料支持，复核只覆盖这些主题，不表示整个宽领域内所有潜在习惯都无变化。材料不足和冲突允许保持未知，不强制形成规律。判断批准不替代 proposals 的独立事实审核和入书收据。'''
EXTRACTION += '''\nbaseline_targets 非空的请求只复核指定具体认识及其范围，独立于同领域其他习惯；空请求用于发现尚未绑定的新模式。material_stats 是有效材料覆盖，不是实际行为频率，缺报日未知。旧低频基线不能因近期材料密集而删除，近期变化与旧基线分别保留。书页版本变化或新材料出现会重开复核，不等待人为长期晋级线。'''
EXTRACTION += '\n' + FREQUENCY_GUIDE


def checkpoint(first_date, source_date):
    first, current = date.fromisoformat(first_date), date.fromisoformat(source_date)
    elapsed = (current - first).days
    if elapsed < 7:
        return None
    offset = 7 if elapsed < 28 else 28 if elapsed < 90 else (elapsed // 90) * 90
    return {'checkpoint_day': offset, 'due_date': (first + timedelta(days=offset)).isoformat()}


def receipt_path(rid):
    # Reuse the existing maintenance record-ID contract, not arbitrary paths.
    maintenance.record_path(rid)
    return maintenance.folder() / 'habit_reviews' / (rid + '.json')


def proof(evidence):
    return {row['id']: books.revision(row['text']) for row in evidence}


def prepare(source_date, session_id, *, leads=None, baseline_source=None, context_sources=False):
    if leads is None:
        from .habit_evidence import leads as chat_leads
        from .device_habit_evidence import leads as device_leads
        leads = [*chat_leads(source_date, session_id, review_days=1, window_days=0),
                 *device_leads(source_date, review_days=1, window_days=0)]
    from .habit_baseline import targets as baseline_targets, material_stats
    snapshot = {domain: books.catalog(domain) for domain in ('self', 'other', 'world')}
    requests, evidence = [], []
    for lead in leads:
        rows = [{**row, 'identity_basis': row.get('identity_basis') or
                 ('producer_device_observation' if row.get('source_kind') == 'sentinel_day_fact'
                  else 'validated_canonical_chat')}
                for row in lead.get('evidence', []) if row.get('active_date')
                and row['active_date'] < source_date]
        if not rows:
            continue
        scheduling = rows if 'schedule_evidence_ids' not in lead else [
            row for row in rows if row['id'] in lead['schedule_evidence_ids']]
        if not scheduling:
            continue
        first = min(row['active_date'] for row in scheduling)
        due = checkpoint(first, source_date)
        if due is None:
            continue
        dimension = lead.get('lead_dimension') or lead.get('device_field')
        if not dimension:
            raise books.BookError('行为复核材料缺少领域归属')
        frequency = frequency_select(lead.get('frequency_evidence', []), rows)
        frequency_proof = maintenance.digest(frequency)
        # An unbound discovery request remains alongside specific baselines;
        # one known habit must not swallow a new interest in the same domain.
        groups = [[], *[[target] for target in baseline_targets(snapshot, lead['subject_id'], dimension)]]
        for bound in groups:
            review_rows, coverage = rows, None
            from .habit_baseline_sources import START_DATE, collect
            if (bound or context_sources) and source_date >= START_DATE:
                linked, coverage = collect(bound, snapshot, session_id, source_date, source=baseline_source,
                    **({'context_anchor_ids': [] if bound else [r['id'] for r in rows if r.get('source_kind', 'private_chat') == 'private_chat']}
                       if context_sources else {}))
                # Context resolution must not relabel an already frozen lead.
                known = {r['id']: r for r in rows}
                for row in linked:
                    if context_sources and row['id'] in known:
                        original = known[row['id']]
                        if any(original.get(k) != row.get(k) for k in ('text', 'speaker', 'timestamp', 'active_date')):
                            raise ValueError('habit_context_anchor_changed')
                review_rows = merge_evidence(rows, [r for r in linked if not context_sources or r['id'] not in known])
            current_proof = proof(review_rows)
            rid = maintenance.digest({'pipeline': contract_version(source_date), 'session': session_id,
                'subject': lead['subject_id'], 'dimension': dimension,
                'first': first, 'checkpoint': due['checkpoint_day'], 'baseline_targets': bound,
                **({'context_contract': 'habit-chat-windows-v1'} if context_sources else {})})
            path = receipt_path(rid)
            if path.exists():
                previous = json.loads(path.read_text('utf8'))
                if (previous.get('status') == 'completed' and previous.get('evidence_proof')
                        and current_proof == previous['evidence_proof']
                        and (coverage is None or coverage == previous.get('source_coverage'))
                        and (not frequency and not previous.get('frequency_proof')
                             or frequency_proof == previous.get('frequency_proof'))):
                    continue
            requests.append({'id': rid, 'subject_id': lead['subject_id'], 'dimension': dimension,
                **({'output_contract': 'habit-source-span-v2'} if context_sources else
                   {'output_contract': 'habit-local-selection-v1'} if source_date >= '2026-10-23' else {}),
                **({'support_contract': 'habit-claim-support-v1', 'entailment_contract':'habit-claim-entailment-v1'}
                   if source_date >= CLAIM_START_DATE else {}),
                'first_material_date': first, **due, 'baseline_targets': bound,
                'material_stats': material_stats(review_rows, as_of=source_date),
                **({'source_coverage': coverage, 'source_session_id': session_id,
                    'review_source_date': source_date} if coverage is not None else {}),
                'frequency_evidence': frequency, 'frequency_proof': frequency_proof,
                'reference_ids': [row['id'] for row in review_rows]})
            evidence.extend(review_rows)
    return {'requests': requests, 'evidence': merge_evidence(evidence)}


def model_requests(requests, n_of, *, local=False, snapshot=None, target_catalogue=None):
    if local:
        from .habit_local_contract import material, enabled
        if any(enabled(r) for r in requests):
            return material(requests, n_of, snapshot, target_catalogue)
    return [{**{key: value for key, value in request.items() if key != 'frequency_proof'},
             'frequency_evidence': model_rows(request.get('frequency_evidence', []), n_of),
             'reference_ids': [n_of[mid] for mid in request['reference_ids']]}
            for request in requests]


def targets(value, subject_id, snapshot=None):
    if not isinstance(value, list) or not value:
        return []
    from .topics import sections
    current = snapshot if snapshot is not None else {domain: books.catalog(domain) for domain in books.DOMAINS}
    result = []
    for target in value:
        if not isinstance(target, dict):
            return []
        domain = target.get('domain')
        if not isinstance(domain, str) or domain not in books.DOMAINS:
            return []
        entry = next((row for row in current.get(domain, [])
                      if row.get('entry_id') == target.get('entry_id')
                      and row.get('revision') == target.get('revision')
                      and row.get('subject_id') == subject_id), None)
        if entry is None or not isinstance(target.get('section'), str) or target.get('section') not in {title for title, _ in sections(entry['body'])}:
            return []
        result.append({key: target[key] for key in ('domain', 'entry_id', 'revision', 'section')})
    return result


def reports(raw, requests, space, snapshot=None):
    """Missing/malformed status cannot invalidate independently valid facts."""
    return report_assessment(raw, requests, space, snapshot)['reports']


def report_assessment(raw, requests, space, snapshot=None):
    """Keep valid siblings and fixed, text-free rejection diagnostics."""
    known = {request['id']: request for request in requests}
    rejected = []
    def reject(code, index=None, rid=None):
        rejected.append({'code': code, **({'row_index': index} if index is not None else {}),
                         **({'request_id': rid} if rid in known else {})})
    try:
        values = parse_response(raw, 'habit_reviews')
    except (ValueError, TypeError, AttributeError):
        return {'reports': [], 'rejections': [{'code': 'report_container_invalid'}]}
    from .habit_local_contract import decode_rows
    current = snapshot if snapshot is not None else {domain: books.catalog(domain) for domain in books.DOMAINS}
    values = decode_rows(values, requests, space, current)
    counts = {rid: sum(isinstance(row, dict) and row.get('id') == rid for row in values)
              for rid in known}
    for rid, count in counts.items():
        if not count:
            reject('report_missing', rid=rid)
    result = []
    for index, row in enumerate(values):
        rid = row.get('id') if isinstance(row, dict) else None
        if not isinstance(rid, str) or rid not in known:
            reject('report_unknown_id', index)
            continue
        if counts[rid] != 1:
            reject('report_duplicate_id', index, rid)
            continue
        if row.get('_local_error'):
            reject(row['_local_error'], index, rid)
            continue
        refs = row.get('reference_ids', [])
        if not isinstance(row.get('outcome'), str) or row['outcome'] not in OUTCOMES:
            reject('outcome_invalid', index, rid)
            continue
        if not isinstance(refs, list) or any(type(n) is not int for n in refs):
            reject('reference_type', index, rid)
            continue
        if any(n < 1 or n > len(space) for n in refs):
            reject('reference_range', index, rid)
            continue
        if not refs and row['outcome'] != 'insufficient':
            reject('required_references_missing', index, rid)
            continue
        if len(set(refs)) != len(refs):
            reject('reference_duplicate', index, rid)
            continue
        if not {space[n - 1]['id'] for n in refs} <= set(known[rid]['reference_ids']):
            reject('reference_out_of_scope', index, rid)
            continue
        bound = targets(row.get('targets'), known[row['id']]['subject_id'], snapshot)
        expected = known[row['id']].get('baseline_targets', [])
        if expected and row['outcome'] == 'supported_no_change' and bound != expected:
            reject('baseline_target_mismatch', index, rid)
            continue
        if row['outcome'] == 'supported_no_change' and not bound:
            reject('target_invalid', index, rid)
            continue
        normalized = {'id': row['id'], 'outcome': row['outcome'], 'targets': bound,
                      'reference_ids': [space[n - 1]['id'] for n in refs]}
        from .habit_local_contract import SPAN_VERSION
        if known[rid].get('output_contract') == SPAN_VERSION:
            normalized['selection_contract'] = SPAN_VERSION
        if known[row['id']].get('support_contract') and row['outcome'] == 'supported_no_change':
            from .habit_claim_support import validate, references
            issues=[]
            claims = validate(row.get('claim_support'), bound, known[row['id']], space, snapshot, indexed=True,issues=issues)
            if claims is None:
                reject('claim_support_invalid', index, rid)
                rejected[-1]['issues']=issues
                continue
            if references(claims) != set(normalized['reference_ids']):
                reject('claim_reference_mismatch', index, rid)
                continue
            normalized.update(claim_support=claims, coverage_scope='cited_claims_only')
        result.append(normalized)
    return {'reports': result, 'rejections': rejected}


def approvals(raw, requests):
    try:
        values = parse_response(raw, 'habit_review_approvals')
    except (ValueError, TypeError, AttributeError):
        return []
    known = {request['id'] for request in requests}
    return [row['id'] for row in values if isinstance(row, dict) and row.get('id') in known
            and row.get('approved') is True
            and sum(isinstance(other, dict) and other.get('id') == row['id'] for other in values) == 1]


def approval_decisions(raw, requests, reports=(), space=()):
    """Persist supplied rejection codes; a historical false has no known reason."""
    try:
        rows = parse_response(raw, 'habit_review_approvals')
    except (ValueError, TypeError, AttributeError):
        return []
    known = {r['id']: r for r in requests}
    report_by_id = {r['id']: r for r in reports}
    decisions = []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] not in known
                or type(row.get('approved')) is not bool
                or sum(isinstance(r, dict) and r.get('id') == row['id'] for r in rows) != 1):
            continue
        code = row.get('reason_code')
        if not isinstance(code, str) or code not in {'unsupported', 'insufficient', 'scope_mismatch', 'unknown'}:
            code = 'legacy_rejection_unexplained'
        decision = {'id': row['id'], 'approved': row['approved'],
                    'reason_code': 'approved' if row['approved'] else code}
        indexes, numbers = row.get('claim_indexes', []), row.get('source_numbers', [])
        count = len(report_by_id.get(row['id'], {}).get('claim_support', []))
        decision['claim_indexes'] = indexes if isinstance(indexes, list) and all(
            type(i) is int and 1 <= i <= count for i in indexes) else []
        decision['source_message_ids'] = [space[n - 1]['id'] for n in numbers] if (
            isinstance(numbers, list) and all(type(n) is int and 1 <= n <= len(space)
                and space[n - 1]['id'] in known[row['id']]['reference_ids'] for n in numbers)) else []
        decisions.append(decision)
    return decisions


def _baseline_changed(plan, title):
    before, after = dict(sections(plan.get('before', ''))), dict(sections(plan.get('after', '')))
    related = companion_titles([title], list({**before, **after}.items()))
    return any(before.get(section) != after.get(section) for section in related)


def finish(requests, reports, approved_ids, items, evidence, *, source_date, session_id, run_id, decisions=()):
    from .habit_baseline import material_stats
    available = {row['id']: row for row in evidence}
    accepted = {row['id']: row for row in reports if row['id'] in approved_ids}
    updated_by_subject, updated_items = {}, []
    for iid in items:
        item = maintenance.get(iid)
        if item.get('status') == 'auto_applied':
            updated_items.append(item)
            updated_by_subject.setdefault(item['proposal']['subject_id'], set()).update(
                ref['message_id'] for ref in item['proposal']['evidence'])
    completed, pending = 0, 0
    for request in requests:
        report = accepted.get(request['id'])
        report_present = any(row['id'] == request['id'] for row in reports)
        approved = request['id'] in approved_ids
        pending_reason = ('missing_valid_report' if not report_present else
                          'review_not_approved' if not approved else '')
        if report and request.get('source_coverage') is not None:
            from .habit_baseline_sources import check_current
            try:
                check_current([request])
            except ValueError:
                report = None
                pending_reason = 'baseline_sources_changed'
        if report:
            refs = report.get('reference_ids', [])
            if (not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs)
                    or len(set(refs)) != len(refs)
                    or not set(refs) <= set(request['reference_ids'])
                    or (not refs and report['outcome'] != 'insufficient')):
                report = None  # Recheck persisted reports from older extraction runs too.
                pending_reason = 'report_reference_scope'
        expected = request.get('baseline_targets', [])
        if expected and report and report['outcome'] == 'supported_no_change' and report.get('targets') != expected:
            report = None
            pending_reason = 'baseline_target_mismatch'
        if expected and report and report['outcome'] != 'updated' and not targets(expected, request['subject_id']):
            report = None
            pending_reason = 'baseline_source_changed'
        if report and report['outcome'] == 'supported_no_change' and not targets(
                report.get('targets'), request['subject_id']):
            report = None  # A stale/unbound book cannot certify unchanged cognition.
            pending_reason = 'no_change_target_unavailable'
        if report and report['outcome'] == 'supported_no_change' and request.get('support_contract'):
            from .habit_claim_support import validate, references
            claims = validate(report.get('claim_support'), report.get('targets', []), request, evidence)
            if claims is None or references(claims) != set(report['reference_ids']):
                report = None
                pending_reason = 'claim_support_unproven'
        if report and report['outcome'] == 'updated' and not (
                set(request['reference_ids']) & set(report.get('reference_ids', []))
                & updated_by_subject.get(request['subject_id'], set())):
            report = None  # Proposed changes rejected or uncommitted are not updates.
            pending_reason = 'update_not_committed'
        if expected and report and report['outcome'] == 'updated':
            for target in expected:
                current = next((entry for entry in books.catalog(target['domain'])
                                if entry['entry_id'] == target['entry_id']), None)
                if not current or not any(item.get('plan', {}).get('domain') == target['domain']
                        and item['plan'].get('name') == current['name']
                        and item['plan'].get('revision') == target['revision']
                        and _baseline_changed(item['plan'], target['section'])
                        and item.get('result_revision') == current['revision']
                        and item['proposal'].get('subject_id') == request['subject_id']
                        and (set(report['reference_ids']) &
                             {ref['message_id'] for ref in item['proposal'].get('evidence', [])})
                        for item in updated_items):
                    report = None
                    pending_reason = 'baseline_update_not_proven'
                    break
        if report:
            from .habit_claim_review import operator_held
            if operator_held(request,report,evidence):
                report = None
                pending_reason = 'operator_evidence_hold'
        status = 'completed' if report else 'pending'
        selected = [available[mid] for mid in request['reference_ids'] if mid in available]
        state = {'id': request['id'], 'status': status, 'source_date': source_date,
                 'report_present': report_present, 'review_approved': approved,
                 'pending_reason': pending_reason,
                 'review_decision': next((d for d in decisions if d.get('id') == request['id']), None),
                 'session_id': session_id, 'run_id': run_id,
                 'first_material_date': request['first_material_date'],
                 'checkpoint_day': request['checkpoint_day'], 'dimension': request['dimension'],
                 'outcome': report['outcome'] if report else 'unconfirmed',
                 'targets': report.get('targets', []) if report else [],
                 'claim_support': report.get('claim_support', []) if report else [],
                 **({'selection_contract': report['selection_contract']}
                    if report and report.get('selection_contract') else {}),
                 'coverage_scope': ('cited_claims_only' if report and report.get('claim_support') else
                                    'legacy_target_report' if report and report['outcome'] == 'supported_no_change' else 'report_outcome'),
                 'baseline_targets': request.get('baseline_targets', []),
                 'material_stats': request.get('material_stats', {}),
                 'reviewed_material_stats': material_stats(
                     [available[mid] for mid in report.get('reference_ids', []) if mid in available], as_of=source_date) if report else {},
                 'evidence_proof': proof(selected),
                 **({'source_coverage': request['source_coverage']} if 'source_coverage' in request else {}),
                 'frequency_proof': request.get('frequency_proof')}
        from .habit_review_scope import state as scope_state
        state['processing_scope'] = scope_state(state)
        from .habit_claim_dispositions import summary as disposition_summary
        summary = disposition_summary([state['review_decision']] if state['review_decision'] else [])
        if summary:
            state['candidate_dispositions'] = summary
        books.atomic_text(receipt_path(request['id']), json.dumps(state, ensure_ascii=False))
        completed += bool(report); pending += not bool(report)
    return {'completed': completed, 'pending': pending}


def status():
    """Text-free persisted review receipts; reading does not schedule a review."""
    rows = [json.loads(path.read_text('utf8'))
            for path in (maintenance.folder() / 'habit_reviews').glob('*.json')]
    fields = ('id', 'status', 'source_date', 'report_present', 'review_approved',
              'pending_reason', 'session_id', 'run_id', 'first_material_date',
              'checkpoint_day', 'dimension', 'outcome', 'coverage_scope', 'candidate_dispositions',
              'material_stats', 'reviewed_material_stats', 'evidence_proof', 'frequency_proof')
    from .habit_review_scope import state as scope_state, counts as scope_counts
    rows = [{**{key: row[key] for key in fields if key in row},
             'processing_scope': scope_state(row),
             'claim_count': len(row.get('claim_support', [])),
             'target_count': len(row.get('targets', [])),
             'baseline_target_count': len(row.get('baseline_targets', []))} for row in rows]
    return {'reviews': rows, 'processing_scope_counts': scope_counts(rows),
            'completed': sum(row.get('status') == 'completed' for row in rows),
            'pending': sum(row.get('status') != 'completed' for row in rows)}
