"""Daily other-book orchestration; preview and commit share one analysis engine."""
import asyncio
import json
from . import books, maintenance, autonomous
from .maintenance_sources import sources
from .other_history import searcher, source_validator
from .brief import defer_updates

_lock = asyncio.Lock()


def _outcome(state):
    """Report both admission and review rejection units, not lost events.

    A cached single-batch result can be read correctly without changing its
    historical receipt, rerunning a model, or reapplying a book update.
    """
    diagnostics = state.get('analysis', {}).get('diagnostics')
    if isinstance(diagnostics, dict):
        validation = len(diagnostics.get('validation_rejections', []))
        reviewed = len(diagnostics.get('rejections', []))
        rejected = validation + reviewed
    else:
        validation = state.get('validation_rejection_count', 0)
        reviewed = state.get('review_rejection_count', 0)
        rejected = state.get('rejected_proposal_count', validation + reviewed)
    topic_rejected = state.get('rejected_topic_annotation_count', 0)
    return {'status': 'completed_with_rejections' if rejected or topic_rejected else state['status'],
            'items': state.get('items', []), 'rejected_proposal_count': rejected,
            'validation_rejection_count': validation, 'review_rejection_count': reviewed,
            **({'rejected_topic_annotation_count': topic_rejected} if topic_rejected else {})}


async def analyse_rows(rows, llm, *, source_date, session_id, stage_state=None, checkpoint=None):
    evidence = sources(rows)
    return await analyse_evidence(evidence, llm, source_date=source_date, session_id=session_id,
                                  stage_state=stage_state, checkpoint=checkpoint)


async def analyse_evidence(evidence, llm, *, source_date, session_id, stage_state=None, checkpoint=None):
    """Analyse already-attributed internal evidence without remapping speakers."""
    from .other_analysis import analyse
    from .other_observations import related
    from .habit_evidence import leads
    from .device_habit_evidence import leads as device_leads
    evidence = [{**e, 'active_date': e.get('active_date') or source_date} for e in evidence]
    if not evidence:
        return {'entries': [], 'entities': [], 'snapshot': books.catalog('other'), 'evidence': [], 'observations': [], 'diagnostics': {'calls': 0}}
    return await analyse(evidence, llm, source_date=source_date, session_id=session_id,
                         search=searcher(session_id, source_date),
                         observations=related(evidence, session_id, source_date) + leads(source_date, session_id)
                                      + device_leads(source_date), stage_state=stage_state, checkpoint=checkpoint,
                         validate_retrieval=source_validator(session_id, source_date))


async def run(rows, llm, *, source_date, session_id):
    evidence = sources(rows)
    return await run_evidence(
        evidence, llm, source_date=source_date, session_id=session_id,
        _analyse=lambda **kwargs: analyse_rows(rows, llm, source_date=source_date, session_id=session_id, **kwargs),
    )


@defer_updates
async def run_evidence(evidence, llm, *, source_date, session_id, _analyse=None, progress_cb=None):
    from . import other_batches
    batches = other_batches.partition(evidence)
    if len(batches) <= 1:
        return await _run_single(evidence, llm, source_date=source_date, session_id=session_id, _analyse=_analyse)
    return await other_batches.run(batches,
        lambda material: _run_single(material, llm, source_date=source_date, session_id=session_id),
        source_date=source_date, session_id=session_id, progress_cb=progress_cb)


async def _run_single(evidence, llm, *, source_date, session_id, _analyse=None):
    """Commit private-chat plus trusted external-interaction evidence."""
    if not evidence: return {'status': 'no_evidence', 'items': []}
    rid = maintenance.digest({'pipeline':'other-daily-v1','date':source_date,'session':session_id,'evidence':evidence})
    path = maintenance.folder() / 'other_runs' / (rid + '.json')
    async with _lock:
        state = json.loads(path.read_text('utf-8')) if path.exists() else {}
        if state.get('status') in {'completed', 'completed_with_rejections'}:
            return {**state, **_outcome(state)}
        state.update(id=rid, source_date=source_date, session_id=session_id, status='running')
        state.pop('error_type', None)
        state.pop('error_detail', None)
        def checkpoint(): books.atomic_text(path, json.dumps(state, ensure_ascii=False))
        checkpoint()
        try:
            if 'analysis' not in state:
                stage_args = {'stage_state': state.setdefault('analysis_stages', {}), 'checkpoint': checkpoint}
                state['analysis'] = await (_analyse(**stage_args) if _analyse else analyse_evidence(
                    evidence, llm, source_date=source_date, session_id=session_id, **stage_args))
                state.pop('analysis_stages', None)  # full reviewed result now owns recovery
                checkpoint()
            result = state['analysis']
            snapshot = result['snapshot']
            if isinstance(snapshot, list): snapshot = {'self': [], 'other': snapshot}
            committed = await autonomous.run_evidence(result['evidence'], llm, source_date=source_date,
                session_id=session_id, origin='other_daily', scope='other', prepared={'snapshot': snapshot,
                'output': {'entities':result.get('entities',[]), 'entries':result['entries']}})
            from .lead_diagnostics import committed_funnel
            committed_funnel(result.get('diagnostics', {}).get('lead_funnel'), committed.get('items', []))
            state.update(status='completed', items=committed.get('items', []))
            state['rejected_topic_annotation_count'] = committed.get('rejected_topic_annotation_count', 0)
            state.update(_outcome(state))
            checkpoint()
            return _outcome(state)
        except books.RevisionConflict:
            state.pop('analysis',None)
            state.pop('analysis_stages',None)
            state.update(status='error', error_type='RevisionConflict'); checkpoint(); raise
        except BaseException as exc:
            # 记下具体原因：只留 error_type 会让提交阶段的失败无从定位（2026-09-11）
            state.update(status='error', error_type=type(exc).__name__, error_detail=str(exc)[:300]); checkpoint(); raise
