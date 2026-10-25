"""Explicit per-turn effort, checked against the executor's model catalog."""
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')


def catalog_options(rows):
    return {row.get('model') or row.get('id'): {
        'supported': [item['reasoningEffort'] for item in row.get('supportedReasoningEfforts', [])
                      if item.get('reasoningEffort') in EFFORTS],
        'default': row.get('defaultReasoningEffort')}
        for row in rows if row.get('model') or row.get('id')}


def validate_catalog(model, effort, options):
    supported = options.get(model, {}).get('supported', [])
    if supported and effort not in supported:
        raise ValueError('reasoning_effort_not_supported:' + model + ':' + effort)
    return effort
