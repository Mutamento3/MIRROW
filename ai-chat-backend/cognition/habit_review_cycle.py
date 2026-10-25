"""Opt-in extraction, scoped review, one correction, then original habit finish.

No book writer, automatic route change, retry of unknown sends, or legacy adoption.
"""
import copy
import json
from . import books, habit_batches as batch, habit_review, habit_local_contract as local
from . import habit_scoped_audit as audit, habit_claim_review as gate
from .habit_cycle_io import Transport, Pending, response
from .prompt_request import CognitionPrompt
from .maintenance_sources import model_materials
from .habit_claim_support import model_report
from memory_v2.day_settlement_checkpoint import _digest

MODE = 'habit-scoped-cycle-v3'
LEGACY_MODE = 'habit-scoped-cycle-v2'
MODES = frozenset({MODE, LEGACY_MODE})
REPAIR = '''这是本请求唯一一次局部修正。完整原始材料和旧栏目保持不变。
review_feedback 是独立审核发现的具体缺口，不是新事实。只修正 failed_cases 对应的认识和引用；accepted_case_ids 对应的认识、目标和证据保持原样。
保留原报告各条认识的位置和数量；为失败项补齐真正支持它的消息，或选择同目标里保留条件、语义完整的更窄旧认识。确实没有可支持的修正时保留该项，等待处理；不删除失败项换取通过。
若原报告为 insufficient，重新检查指出的反例，可改成有证据的 supported_no_change。若没有任何已通过认识，也可将原肯定报告改成 insufficient，随后仍须完整情境独立审核。
返回与初提取相同的 habit_reviews 合同；本阶段不批准、不写书。'''


def fresh(request, space, snapshot):
    batch.check_sources([request], space)
    current = {d: books.catalog(d) for d in books.DOMAINS}
    chosen = local.choices(request, snapshot)
    if (not chosen or any(t not in [c['target'] for c in chosen] for t in request.get('baseline_targets', []))
            or chosen != local.choices(request, current)):
        raise Pending('cycle_book_version_changed')


def unheld(request, record, space):
    for report in [*record.get('structural_ancestors', []), *record.get('report_lineage', [])]:
        if gate.operator_held(request, report, space):
            raise Pending('cycle_ancestor_operator_hold')


def parsed(raw, request, space, snapshot):
    result = habit_review.report_assessment(raw, [request], space, snapshot)
    if result['rejections'] or len(result['reports']) != 1:
        raise Pending('cycle_report_contract_failed')
    report = result['reports'][0]
    if report['outcome'] not in {'supported_no_change', 'insufficient'}:
        raise Pending('cycle_requires_factual_proposal_workflow')
    return report


async def run(state, requests, space, snapshot, checkpoint, *, source_date, run_id,
              transport=None, max_calls=4, max_provider_bytes=125000, mode=None, max_cost_cny=None):
    if type(max_calls) is not int or not 0 <= max_calls <= 96:
        raise ValueError('cycle_call_limit_invalid')
    if type(max_provider_bytes) is not int or not 1 <= max_provider_bytes <= 2000000:
        raise ValueError('cycle_size_limit_invalid')
    transport = transport or Transport()
    mode = mode or state.get('extraction_material', {}).get('habit_mode') or state.get('habit_scoped_cycle', {}).get('version') or MODE
    if mode not in MODES:
        raise ValueError('cycle_version_unsupported')
    cycle = state.setdefault('habit_scoped_cycle', {'version': mode, 'components': {}})
    if cycle['version'] != mode:
        raise ValueError('cycle_version_changed')
    if max_cost_cny is not None:
        if max_cost_cny != '8.00' or mode != MODE:
            raise ValueError('cycle_cost_policy_invalid')
        if cycle.get('cost_limit_cny', max_cost_cny) != max_cost_cny:
            raise ValueError('cycle_cost_policy_changed')
        if 'cost_limit_cny' not in cycle and cycle['components']:
            raise ValueError('cycle_cost_policy_cannot_adopt_existing_attempts')
        cycle['cost_limit_cny'] = max_cost_cny
    elif cycle.get('cost_limit_cny') is not None:
        raise ValueError('cycle_cost_policy_missing')
    reports, approved, decisions, deferred = [], [], [], []
    for request in requests:
        rid = request['id']
        record = None
        try:
            if (request.get('output_contract') != local.SPAN_VERSION
                    or request.get('evidence_selection_contract') != local.MESSAGE_EVIDENCE
                    or request.get('entailment_contract') != gate.VERSION):
                raise Pending('cycle_requires_explicit_full_message_contract')
            prompt, selected = batch.component(request, space, snapshot, [], source_date=source_date, run_id=run_id,
                                                canonical_payload=mode == MODE)
            binding = _digest({'mode': mode, 'request': request, 'space': selected,
                               'targets': local.choices(request, snapshot), 'extract_wire': prompt.wire()})
            record = cycle['components'].setdefault(rid, {'binding': binding, 'stages': {}})
            if record['binding'] != binding:
                raise Pending('cycle_input_changed')
            fresh(request, selected, snapshot)
            unheld(request, record, selected)

            async def call(name, prompt):
                fresh(request, selected, snapshot)
                unheld(request, record, selected)
                raw = await response(cycle, record, name, prompt, transport, checkpoint,
                                     max_calls=max_calls, max_bytes=max_provider_bytes)
                fresh(request, selected, snapshot)
                unheld(request, record, selected)
                return raw

            raw_initial = await call('extract', prompt)
            known_initial = habit_review.report_assessment(raw_initial, [request], selected, snapshot)['reports']
            if 'structural_ancestors' in record and record['structural_ancestors'] != known_initial:
                raise Pending('cycle_structural_ancestors_changed')
            record.setdefault('structural_ancestors', copy.deepcopy(known_initial))
            checkpoint()
            # A malformed sibling cannot hide a valid held parent from a repair.
            unheld(request, record, selected)
            structural_repair = False
            try:
                initial = parsed(raw_initial, request, selected, snapshot)
            except Pending as exc:
                if str(exc) != 'cycle_report_contract_failed': raise
                structural_repair = True
                repair_payload = json.loads(prompt.payload_json)
                repair_payload.update(previous_output=raw_initial,
                    validation=habit_review.report_assessment(raw_initial, [request], selected, snapshot)['rejections'])
                repair_prompt = CognitionPrompt(habit_review.extraction_contract([request])+
                    '\n这次原返回未通过确定性合同校验。依据validation定位问题，使用完整原始材料修正报告；'
                    '每条肯定认识选择实际支持的完整消息编号。无证据的旧认识保留未复核，不用空证据认证。'
                    '这是本请求唯一一次修正，返回habit_reviews。', repair_payload,
                    stage='habit_report_repair', source_date=source_date, run_id=run_id,
                    canonical_payload=mode == MODE)
                initial = parsed(await call('repair_once', repair_prompt), request, selected, snapshot)
            if 'report_lineage' in record and record['report_lineage'][0] != initial:
                raise Pending('cycle_initial_report_changed')
            record.setdefault('report_lineage', [copy.deepcopy(initial)])
            unheld(request, record, selected)
            initial_cases = audit.cases(request, initial, selected, snapshot)

            async def review(name, rows):
                payload = audit.payload(rows, selected, local.choices(request, snapshot))
                p = CognitionPrompt(audit.CONTRACT, payload, stage='habit_claim_review',
                                    source_date=source_date, run_id=run_id, canonical_payload=mode == MODE)
                raw = await call(name, p)
                return audit.assess(raw, rows, selected)

            checked = await review('review_initial', initial_cases)
            accepted = {key for key, row in checked.items() if row['verdict'] == 'supported'}
            failed = [r for r in initial_cases if r['id'] not in accepted]
            final, final_cases = initial, initial_cases
            dispositions = []
            if failed and not structural_repair:
                # Freeze the first semantic rejection. No later revision can reset the allowance.
                payload = json.loads(prompt.payload_json)
                n_of = {e['id']: n for n, e in enumerate(selected, 1)}
                payload.update(previous_report=model_report(initial, n_of),
                    failed_cases=audit.payload(failed, selected, local.choices(request, snapshot))['cases'], accepted_case_ids=sorted(accepted),
                    review_feedback=[checked[r['id']] for r in failed])
                repair_contract = REPAIR
                if mode == MODE and initial['outcome'] == 'supported_no_change':
                    from . import habit_claim_dispositions as disposition
                    payload['claim_cases'] = audit.payload(initial_cases, selected, local.choices(request, snapshot))['cases']
                    repair_contract = disposition.CONTRACT
                repair_prompt = CognitionPrompt(habit_review.extraction_contract([request])+'\n'+repair_contract,
                    payload, stage='habit_report_repair', source_date=source_date, run_id=run_id,
                    canonical_payload=mode == MODE)
                if initial['outcome'] == 'insufficient':
                    counter = checked[failed[0]['id']]['counterexample']
                    # Explicit reviewer candidate replaces an extra re-extraction
                    # call. It receives a fresh independent review below.
                    candidate = {'habit_reviews': [{'q':1, 'outcome':'supported_no_change',
                                 'claims':[counter], 'sources':[]}]}
                    final = parsed(json.dumps(candidate, ensure_ascii=False), request, selected, snapshot)
                    record['correction_kind'] = 'reviewer_counterexample_independently_rechecked'
                else:
                    raw_revision = await call('repair_once', repair_prompt)
                    final = parsed(raw_revision, request, selected, snapshot)
                final_cases = audit.cases(request, final, selected, snapshot)
                final_ids = {r['id'] for r in final_cases}
                if accepted - final_ids:
                    raise Pending('cycle_correction_changed_accepted_claim')
                if mode == MODE and initial['outcome'] == 'supported_no_change':
                    final_cases, dispositions = disposition.reconcile(
                        raw_revision, initial_cases, final_cases, checked, final['outcome'])
                elif (initial['outcome'] == final['outcome'] == 'supported_no_change'
                        and len(initial_cases) != len(final_cases)):
                    raise Pending('cycle_correction_dropped_or_added_claim')
                if mode == LEGACY_MODE and initial['outcome'] == final['outcome'] == 'supported_no_change':
                    for before, after in zip(initial_cases, final_cases):
                        if before['id'] in accepted:
                            if before != after:
                                raise Pending('cycle_correction_changed_accepted_claim')
                        elif (before['target'] != after['target'] or not (
                                before['statement'] in after['statement'] or after['statement'] in before['statement'])):
                            raise Pending('cycle_correction_changed_question')
                        elif before['id'] != after['id']:
                            after['revision_of'] = {'statement': before['statement'],
                                'target': before['target'], 'feedback': checked[before['id']]}
                            after['id'] = _digest(after)
                if len(record['report_lineage']) > 1 and record['report_lineage'][1] != final:
                    raise Pending('cycle_revised_report_changed')
                if len(record['report_lineage']) == 1:
                    record['report_lineage'].append(copy.deepcopy(final))
                checkpoint()
                unheld(request, record, selected)
                changes = [r for r in final_cases if r['id'] not in checked]
                if changes:
                    checked.update(await review('review_revision', changes))
            unheld(request, record, selected)
            fresh(request, selected, snapshot)
            ok = all(checked[r['id']]['verdict'] == 'supported' for r in final_cases)
            record.update(status='approved' if ok else 'pending', final_report=final,
                final_case_ids=[r['id'] for r in final_cases], checks=checked,
                claim_dispositions=dispositions,
                correction_used=bool(failed) or structural_repair,
                structural_repair_used=structural_repair, coverage_scope=final.get('coverage_scope', 'material_insufficient'))
            reports.append(final)
            if ok: approved.append(rid)
            decisions.append({'id': rid, 'approved': ok, 'contract': audit.VERSION,
                'approval_origin': 'scoped_cycle_model_review', 'reason_code': 'supported' if ok else 'revision_not_supported',
                'claim_dispositions': dispositions,
                'claims': [checked[r['id']] for r in final_cases]})
            if not ok: deferred.append({'id': rid, 'reason': 'revision_not_supported'})
        except Exception as exc:
            reason = str(exc) if isinstance(exc, Pending) else type(exc).__name__
            deferred.append({'id': rid, 'reason': reason})
            if record is not None: record.update(status='pending', pending_reason=reason)
        finally:
            checkpoint()
    cycle.update(approved_ids=approved, decisions=decisions, deferred=deferred,
                 status='completed' if len(approved) == len(requests) else 'pending',
                 model_calls_reserved=sum(s.get('output', {}).get('request_dispatched') is not False
                    for r in cycle['components'].values() for s in r.get('stages', {}).values()))
    state['habit_review_reports'] = reports
    state['habit_review_approved_ids'] = approved
    state['habit_review_decisions'] = decisions
    checkpoint()
    return cycle
