"""Separate foreground work from background scan diagnostics, without changing outcomes."""

TERMINAL = {'completed', 'error'}


def _item(name, state):
    return {
        'name': name, 'label': state.get('label', ''), 'purpose': state.get('purpose', ''),
        'status': state.get('status', 'pending'), 'progress': state.get('progress', 0),
        'message': state.get('message', ''), 'stalled': state.get('stalled', False),
        'details': [{key: stage[key] for key in
                     ('name', 'label', 'status', 'reused', 'started_at', 'completed_at') if key in stage}
                    for stage in state.get('details', []) if isinstance(stage, dict)],
    }


def project(tasks):
    visible, background = [], []
    for name, state in tasks.items():
        target = background if state.get('progress_visible') is False else visible
        target.append(_item(name, state))
    done = sum(item['status'] in TERMINAL for item in visible)
    return {
        'tasks': visible, 'background_tasks': background,
        'all_done': done == len(visible),
        'progress': round(done / len(visible) * 100) if visible else 100,
    }
