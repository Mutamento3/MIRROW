"""Text-free habit diagnostics for this run; never a retry or completion gate."""
from collections import Counter


FIELDS = ('habit_review', 'habit_complete', 'habit_diagnostics')
REJECTION_CODES = frozenset('''report_container_invalid report_missing report_unknown_id
report_duplicate_id outcome_invalid reference_type reference_range required_references_missing
reference_duplicate reference_out_of_scope baseline_target_mismatch target_invalid
claim_support_invalid claim_reference_mismatch report_rejected'''.split())


def rejection_counts(rows):
    counts = Counter()
    for row in rows:
        code = row.get('code')
        counts[code if code in REJECTION_CODES else 'report_rejected'] += row.get('count', 1)
    return [{'code': code, 'count': count} for code, count in sorted(counts.items())]


def public_fields(state):
    """Project private run data or an already projected lane without source text."""
    # Older error roots may only have kept the successful world's lane receipt.
    world = state.get('lane_results', {}).get('world', {})
    saved = state.get('habit_diagnostics', world.get('habit_diagnostics', {}))
    result, diagnostics = {}, {}
    counts = state.get('habit_review', world.get('habit_review'))
    if counts is not None:
        result['habit_review'] = {key: counts[key] for key in ('completed', 'pending')}
        result['habit_complete'] = counts['pending'] == 0
    if 'habit_report_rejections' in state or 'extraction' in saved:
        rows = state.get('habit_report_rejections', saved.get('extraction', {}).get('rejections', []))
        diagnostics['extraction'] = {'rejections': rejection_counts(rows)}
    repair = state.get('habit_report_repair', saved.get('repair'))
    if repair is not None:
        diagnostics['repair'] = {
            'status': repair['status'] if repair.get('status') in {
                'started', 'completed', 'incomplete', 'failed'} else 'unknown',
            'requested_count': (len(repair['requested_ids']) if 'requested_ids' in repair
                                else repair.get('requested_count', 0)),
            'recovered_count': repair.get('recovered_count', 0),
            'rejections': rejection_counts(repair.get('rejections', [])),
        }
    review = state.get('habit_claim_review', saved.get('review'))
    batch = state.get('habit_report_batches', saved.get('batches'))
    if batch is not None:
        diagnostics['batches'] = {key: batch.get(key, 0) for key in
            ('requested_count', 'recovered_count', 'dispatched_count')}
        diagnostics['batches'].update(status=batch.get('status', 'unknown'),
            deferred_count=len(batch['deferred']) if 'deferred' in batch else batch.get('deferred_count', 0),
            refused_count=(sum(r.get('reason') == 'provider_refusal' for r in batch['deferred'])
                           if 'deferred' in batch else batch.get('refused_count', 0)))
    if review is not None:
        diagnostics['review'] = {
            'status': review['status'] if review.get('status') in {
                'not_required', 'reviewed', 'pending'} else 'unknown',
            'deferred_count': review.get('deferred_count', 0),
        }
        if review.get('budget') is not None:
            diagnostics['review']['budget'] = {key: review['budget'][key] for key in (
                'limit_cny', 'known_usage_estimate_cny', 'unknown_attempts', 'unknown_reserve_cny', 'basis')}
    from .habit_claim_dispositions import summary as disposition_summary
    disposition = disposition_summary(state.get('habit_review_decisions', []))
    if disposition:
        diagnostics['candidate_dispositions'] = disposition
    elif 'habit_review_decisions' not in state and saved.get('candidate_dispositions'):
        prior = saved['candidate_dispositions']
        diagnostics['candidate_dispositions'] = {
            **{key: prior.get(key, 0) for key in ('retain', 'revise', 'withdraw', 'defer')},
            'scope': 'this_candidate_only_old_book_unchanged',
        }
    if diagnostics:
        result['habit_diagnostics'] = diagnostics
    return result
