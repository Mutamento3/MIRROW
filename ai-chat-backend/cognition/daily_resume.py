"""Resume only unfinished lanes of the same content-addressed daily source."""
import json
from .habit_diagnostics import FIELDS as HABIT_FIELDS

COMPLETED = {'completed', 'completed_with_rejections', 'no_evidence', 'not_triggered'}
FIELDS = ('status', 'items', 'rejected_proposal_count', 'discarded_review_count',
          'rejected_topic_annotation_count', 'reference_review', *HABIT_FIELDS)


def lane_receipt(result):
    return {key: result[key] for key in FIELDS if key in result}


def completed_results(path, *, rid, source_date, session_id):
    if not path.exists():
        return {}
    from .books import BookError
    try:
        state = json.loads(path.read_text('utf-8'))
    except (ValueError, OSError) as exc:
        raise BookError('认知日结回执无法读取，保留原件') from exc
    if (state.get('id'), state.get('source_date'), state.get('session_id')) != (rid, source_date, session_id):
        raise BookError('认知日结回执来源不匹配，保留原件')
    return {name: lane_receipt(result) for name, result in state.get('lane_results', {}).items()
            if isinstance(result, dict) and result.get('status') in COMPLETED
            and state.get('lanes', {}).get(name) == result.get('status')}
