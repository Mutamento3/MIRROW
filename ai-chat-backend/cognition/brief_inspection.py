"""Local UI inspection of the existing brief; no model, writes or new authority."""
from . import books, brief
from .brief_contract import VERSION, material
from .profile_scopes import LABELS, TASK_SCOPES
from .brief_selection import render as render_selection


def inspect():
    """Catalog scans are confined to inspection, never the fast reply reader."""
    base = {'subjects': dict(books.SUBJECTS), 'current_version': VERSION,
            'scopes': dict(LABELS), 'consumers': {key: list(value) for key, value in TASK_SCOPES.items()},
            'source': '现行认知书的简短投影；原书仍是详细认识的来源',
            'identities': [], 'capsules': [], 'sources': [], 'injection_available': False}
    try:
        with books.LOCK:
            data = brief._read()
            if data is None:
                return {**base, 'status': 'missing'}
            # Compare actual authority, including after a process restart or an
            # out-of-band book edit. Inspection does not mark it clean or dirty.
            current = brief.source_refs(material(brief.snapshot()))
            recorded = data['sources']
            if not isinstance(recorded, list) or not isinstance(data['capsules'], list):
                raise ValueError('invalid brief')
            by_entry = {(row['domain'], row['entry_id']): row for row in current}
            sources = []
            for row in recorded:
                actual = by_entry.get((row['domain'], row['entry_id']))
                redirect = None
                if actual is None:
                    from .consolidation import resolve
                    try:
                        resolved = resolve(row['domain'], row['entry_id'])
                        if resolved['entry_id'] != row['entry_id']:
                            redirect = {key: resolved[key] for key in ('domain', 'entry_id', 'name', 'revision')}
                    except books.BookError:
                        pass
                sources.append({**{key: row.get(key, '') for key in (
                    'n', 'domain', 'entry_id', 'name', 'subject_id', 'basis', 'revision')},
                    'current_revision': actual['revision'] if actual else '',
                    'redirect': redirect,
                    'state': 'merged' if redirect else 'missing' if actual is None else
                             'current' if actual['revision'] == row['revision'] else 'changed'})
            with brief._guard:
                pending = str(books.ROOT) in brief._dirty_roots
            from .brief_serving import unchanged_view
            serving, availability = unchanged_view(data, current)
            unchanged = availability.pop('unchanged_source_numbers')
            status = ('stale' if recorded != current else 'refreshing' if pending else
                      'previous' if data['version'] != VERSION else 'current')
            available = bool(serving['identities'] or availability['available_point_count'])
            if status == 'stale' and available:
                status = 'partial'
            return {**base, 'status': status,
                    'injection_available': available, **availability,
                    'version': data['version'], 'generated_at': data.get('generated_at', ''),
                    'source_date': data.get('source_date', ''),
                    'scope_audit': data.get('scope_audit', []),
                    'identities': data['identities'],
                    'capsules': [{**capsule, 'points': [{**point, 'display_text': render_selection(point),
                                                       'injection_available': bool(point.get('references')) and
                                                           all(n in unchanged for n in point['references'])}
                                                       for point in capsule['points']]}
                                 for capsule in data['capsules']], 'sources': sources}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # No paths, source prose, credentials or raw exceptions in the error.
        return {**base, 'status': 'unavailable'}
