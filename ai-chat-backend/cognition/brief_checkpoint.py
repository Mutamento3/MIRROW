"""One replaceable, source-bound brief candidate; never a serving authority."""
import json
from uuid import uuid4
from . import books


def binding(version, source_revision, sources, projection_contract, review_contract):
    return {'version': version, 'source_revision': source_revision, 'sources': sources,
            'projection_contract_hash': books.revision(projection_contract),
            'review_contract_hash': books.revision(review_contract)}


def path(root):
    return root / 'data/cognition_brief/candidate.json'


def _digest(payload):
    return books.revision(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def load(root, expected):
    target = path(root)
    if not target.exists():
        return None
    try:
        record = json.loads(target.read_text('utf-8'))
        if not isinstance(record, dict):
            raise ValueError()
        if record.get('binding') != expected:
            return None
        payload = record['candidate']
        if (record.get('format') != 1 or not isinstance(payload, dict)
                or record.get('candidate_hash') != _digest(payload)
                or any(type(payload.get(key)) is not int or payload[key] < 0
                       for key in ('rejected_count', 'retained_count'))):
            raise ValueError()
        return {**payload, 'generation_id': record.get('generation_id', record['candidate_hash'])}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise books.BookError('认知简版候选检查点无法校验，保留原件') from exc


def save(root, expected, result, *, rejected_count, retained_count):
    payload = {'result': result, 'rejected_count': rejected_count,
               'retained_count': retained_count}
    generation_id = uuid4().hex
    books.atomic_text(path(root), json.dumps({'format': 1, 'binding': expected, 'generation_id': generation_id,
        'candidate': payload, 'candidate_hash': _digest(payload)}, ensure_ascii=False))
    return generation_id


def failed(root, expected):
    payload = load(root, expected)
    if payload is None:
        return False
    record = json.loads(path(root).read_text('utf-8'))
    return (record.get('binding') == expected
            and record.get('review_failed_candidate_hash') == _digest(
                {k: v for k, v in payload.items() if k != 'generation_id'}))


def record_failure(root, expected, *, clear=False):
    payload = load(root, expected)
    if payload is None:
        return
    record = json.loads(path(root).read_text('utf-8'))
    if (record.get('binding') != expected or record.get('candidate_hash') != _digest(
            {k: v for k, v in payload.items() if k != 'generation_id'})):
        return
    if clear:
        if 'review_failed_candidate_hash' not in record:
            return
        record.pop('review_failed_candidate_hash')
    else:
        record['review_failed_candidate_hash'] = record['candidate_hash']
    books.atomic_text(path(root), json.dumps(record, ensure_ascii=False))
