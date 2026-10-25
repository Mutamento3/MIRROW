"""Explicit continuation of opt-in batches; fact commits stay untouched."""
import json
from . import habit_batches, habit_review, habit_claim_review
from .raw_output import capture
from .prompt_request import CognitionPrompt
from .maintenance_sources import model_materials
from .habit_claim_support import model_report
from .world_materials import numbered_references, REFERENCE_CONTRACT
from .factual import REVIEW
from memory_v2.day_settlement_checkpoint import _digest


async def resume(state, llm, checkpoint, *, transport=None, max_new_calls=habit_batches.MAX_NEW_CALLS):
    frozen = state['extraction_material']
    requests, space, snapshot = frozen['review_requests'], frozen['space'], frozen['snapshot']
    proposals = state['proposals']
    # Initial ordinary approval owns only the exact reports it saw. Persist this
    # boundary before adding reports, including across a later interrupted resume.
    prior = state.setdefault('habit_batch_initial_reports', {
        r['id']: _digest(r) for r in state.get('habit_review_reports', [])})
    checkpoint()
    await habit_batches.extract(state, requests, space, snapshot, proposals, checkpoint,
        source_date=state['source_date'], run_id=state['id'], transport=transport, max_new_calls=max_new_calls)
    by_id = {r['id']: r for r in requests}
    pending = []
    outputs = state.setdefault('habit_batch_general_reviews', {})
    for report in state.get('habit_review_reports', []):
        rid = report['id']; request = by_id[rid]
        if habit_claim_review.eligible_report(request, report) or prior.get(rid) == _digest(report):
            continue
        # A new report cannot inherit any earlier general approval/decision.
        state['habit_review_approved_ids'] = [i for i in state.get('habit_review_approved_ids', []) if i != rid]
        state['habit_review_decisions'] = [d for d in state.get('habit_review_decisions', []) if d['id'] != rid]
        checkpoint()
        try:
            _, selected = habit_batches.component(request, space, snapshot, proposals,
                source_date=state['source_date'], run_id=state['id'])
            habit_batches.check_sources([request], selected)
            n_of = {e['id']: n for n, e in enumerate(selected, 1)}
            relevant = [p for p in proposals if all(e['message_id'] in n_of for e in p.get('evidence', []))]
            prompt = CognitionPrompt(REVIEW + REFERENCE_CONTRACT + '\n' + habit_review.review_contract([request]),
                {'source_date': state['source_date'], 'books': snapshot, 'messages': model_materials(selected),
                 'proposals': numbered_references(relevant, n_of, {e['id']: e['text'] for e in selected}),
                 'habit_review_requests': habit_review.model_requests([request], n_of),
                 'habit_reviews': [model_report(report, n_of)]},
                stage='world_review', source_date=state['source_date'], run_id=state['id'])
            raw = await capture(outputs, rid, prompt, llm, checkpoint)
            value = json.loads(raw)
            rows = value.get('habit_review_approvals')
            if (not isinstance(rows, list) or len(rows) != 1 or rows[0].get('id') != rid
                    or type(rows[0].get('approved')) is not bool):
                raise ValueError('habit_batch_general_review_identity')
            habit_batches.check_sources([request], selected)
            state['habit_review_approved_ids'].extend(habit_review.approvals(raw, [request]))
            state['habit_review_decisions'].extend(habit_review.approval_decisions(raw, [request], [report], selected))
        except Exception as exc:
            pending.append({'id': rid, 'error_type': type(exc).__name__})
        finally:
            checkpoint()
    state['habit_batch_general_pending'] = pending
    checkpoint()
