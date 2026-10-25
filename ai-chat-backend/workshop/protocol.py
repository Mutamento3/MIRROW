"""A terminal visit marker is separate from structured execution approval."""
import re


def dialogue(raw):
    lines = raw.rstrip().splitlines()
    if not lines:
        return "", None
    # A quoted/code example is not a current output control.
    fenced = sum(line.strip().startswith('```') for line in lines[:-1]) % 2
    last = lines[-1].strip()
    if fenced or last not in ('*continue*', '*end*'):
        return raw.strip(), None
    body = '\n'.join(lines[:-1]).strip()
    in_code = False
    for line in lines[:-1]:
        if line.strip().startswith('```'):
            in_code = not in_code
        elif not in_code and re.fullmatch(r'\*(continue|end)\*', line.strip()):
            return body, None
    return body, last[1:-1]


def quota_bucket(payload, model):
    """Preserve bucket identity; ambiguous buckets remain unavailable."""
    buckets = payload.get('rateLimitsByLimitId')
    if buckets:
        candidates = [(key, value) for key, value in buckets.items() if key == model or model in (value.get('models') or [])]
        # Codex's generic account bucket applies across supported Codex models.
        if not candidates and 'codex' in buckets:
            candidates = [('codex', buckets['codex'])]
        if len(candidates) != 1:
            return '', {}
        return candidates[0]
    else:
        bucket = payload.get('rateLimits') or {}
        return bucket.get('limitId') or 'codex', bucket


def quota_remaining(payload, model):
    """Choose the actual model bucket; ambiguous buckets remain unknown."""
    _, bucket = quota_bucket(payload, model)
    windows = [bucket.get(key) for key in ('primary', 'secondary')]
    values = [100-float(window['usedPercent']) for window in windows if isinstance(window, dict) and isinstance(window.get('usedPercent'), (int, float))]
    return max(0, min(values)) if values else None
