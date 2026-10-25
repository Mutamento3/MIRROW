"""Explicit scoped upkeep, separate from replaying a completed daily lane."""
from . import books


def commit_classification(entry, proposal, *, run_id):
    """CAS/idempotent metadata write; the authoritative prose is unchanged."""
    if set(proposal) != {'entry_id', 'revision', 'topics'}:
        raise books.BookError('分类复核提案结构无效')
    if (proposal['entry_id'], proposal['revision']) != (entry['entry_id'], entry['revision']):
        raise books.RevisionConflict('分类复核书页版本不匹配')
    receipt = 'topic_annotation_review:' + run_id
    with books.LOCK:
        if books.receipt_present(entry['domain'], entry['name'], receipt):
            current = next(e for e in books.catalog(entry['domain']) if e['entry_id'] == entry['entry_id'])
            return current
        current = next((e for e in books.catalog(entry['domain']) if e['entry_id'] == entry['entry_id']), None)
        if current is None or current['revision'] != entry['revision'] or current['body'] != entry['body']:
            raise books.RevisionConflict('分类复核期间正文已变化，未覆盖')
        return books.save(entry['domain'], entry['name'], {**entry, 'topics': proposal['topics']},
            receipt=receipt, editor='AI 主题分类补修（正文不变）')


async def recheck_legacy_self(rows, llm, *, source_date, session_id):
    from .autonomous import run_evidence
    from .maintenance_sources import sources, merge_evidence
    evidence = sources(rows, include_wander_activities=True)
    return await run_evidence(evidence, llm, source_date=source_date, session_id=session_id,
                             scope='self', origin='legacy_reference_recheck')


def resolve_lane(parent, lane, result, *, resolution):
    """Retain historical rejects; close only a verified current maintenance task.

The original candidate record remains unchanged for a legacy material recheck.
It is retired as unrecoverable original prose, not declared reference-repaired.
"""
    if result.get('status') != 'completed' or result.get('rejected_proposal_count', 0) or result.get('rejected_topic_annotation_count', 0):
        raise books.BookError('维护仍有隔离项，保留原日结提醒')
    if resolution['kind'] not in {'topic_annotations_reviewed', 'legacy_material_rechecked',
                                  'topic_annotation_superseded_by_current_revision'}:
        raise books.BookError('隔离维护结算方式无效')
    if lane not in {'self', 'interest'}:
        raise books.BookError('隔离维护分路无效')
    parent.setdefault('historical_lane_isolations', {}).setdefault(lane, {
        'status': parent['lanes'][lane],
        'rejections': parent.get('lane_rejections', {}).get(lane, {}),
        'reference_diagnostics': parent.get('lane_reference_diagnostics', {}).get(lane, [])})
    parent.setdefault('lane_isolation_resolutions', {})[lane] = resolution
    parent['lanes'][lane] = 'completed'
    from .daily_resume import lane_receipt
    parent.setdefault('lane_results', {})[lane] = lane_receipt(result)
    parent.setdefault('lane_rejections', {})[lane] = {'proposal_count': 0, 'review_count': 0}
    parent.get('lane_reference_diagnostics', {}).pop(lane, None)
    if all(s in {'completed', 'no_evidence', 'not_triggered'} for s in parent['lanes'].values()):
        parent['status'] = 'completed'
