"""Serve only unchanged source-bound parts of an approved profile."""
import json


def unchanged_view(data, current_sources):
    """Pure projection; source numbers belong to the saved profile, not today's order."""
    indexed = {(row['domain'], row['entry_id']): row for row in current_sources}
    unchanged = set()
    for old in data.get('sources', []):
        current = indexed.get((old['domain'], old['entry_id']))
        if current and all(old.get(key) == current.get(key) for key in
                           ('revision', 'subject_id', 'basis', 'name')):
            unchanged.add(old['n'])

    def available(item):
        refs = item.get('references')
        return bool(refs) and all(n in unchanged for n in refs)

    identities = [item for item in data['identities'] if available(item)]
    capsules = [{**capsule, 'points': [p for p in capsule['points'] if available(p)]}
                for capsule in data['capsules']]
    point_count = sum(len(c['points']) for c in capsules)
    total = sum(len(c['points']) for c in data['capsules'])
    return {**data, 'identities': identities, 'capsules': capsules}, {
        'available_point_count': point_count,
        'withheld_point_count': total - point_count,
        'available_identity_count': len(identities),
        'withheld_identity_count': len(data['identities']) - len(identities),
        'unchanged_source_numbers': unchanged,
    }


def merge_unchanged_points(candidate, previous, current_sources):
    """Old valid knowledge stays a review candidate, not an irrevocable approval.

    Reference numbers are snapshot-local. Rebind by the unchanged book identity,
    preserving the source words and temporal qualification without regenerating them.
    """
    if not previous:
        return candidate, 0
    valid, _ = unchanged_view(previous, current_sources)
    current = {(s['domain'], s['entry_id']): s['n'] for s in current_sources}
    rebound = {s['n']: current.get((s['domain'], s['entry_id'])) for s in previous['sources']}
    old = {(c['subject_id'], c['scope']): c['points'] for c in valid['capsules']}
    def key(point):
        return (point['text'], tuple(point['references']),
                json.dumps(point.get('temporal', {}), sort_keys=True))
    capsules, retained = [], 0
    for capsule in candidate['capsules']:
        points = list(capsule['points'])
        seen = {key(p) for p in points}
        for point in old.get((capsule['subject_id'], capsule['scope']), []):
            if 'selection' not in point:
                continue  # Legacy free-form profiles are not literal candidates.
            point = {**point, 'references': [rebound[n] for n in point['references']],
                     'selection': {**point['selection'], 'reference': rebound[point['selection']['reference']]}}
            if key(point) not in seen:
                points.append(point); seen.add(key(point)); retained += 1
        capsules.append({**capsule, 'points': points})
    return {**candidate, 'capsules': capsules}, retained
