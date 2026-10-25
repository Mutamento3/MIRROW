"""Remove exact contained selections, never infer semantic equivalence."""
import json


def deduplicate(result):
    capsules, removed = [], 0
    for capsule in result['capsules']:
        points = capsule['points']
        keep = []
        def identity(p):
            return (tuple(p.get('references', [])), p.get('source_voice'),
                    json.dumps(p.get('temporal', {}), sort_keys=True),
                    p.get('selection', {}).get('reference'))
        for i, point in enumerate(points):
            quote = point.get('selection', {}).get('quote')
            redundant = bool(quote) and any(
                i != j and identity(point) == identity(other)
                and quote in other.get('selection', {}).get('quote', '')
                and (len(other['selection']['quote']) > len(quote) or j < i)
                for j, other in enumerate(points))
            if redundant:
                removed += 1
            else:
                keep.append(point)
        capsules.append({**capsule, 'points': keep})
    return {**result, 'capsules': capsules}, removed
