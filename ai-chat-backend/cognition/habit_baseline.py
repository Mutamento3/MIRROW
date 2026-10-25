"""Specific book-backed review targets; no inferred habits or second ledger."""
from datetime import date, timedelta
from . import topics


def targets(snapshot, subject_id, dimension):
    result = []
    for domain, entries in snapshot.items():
        if domain not in {'world', 'other', 'self'}:
            continue
        for entry in entries:
            if entry.get('subject_id') != subject_id or entry.get('state', 'active') != 'active':
                continue
            for theme in topics.project(entry):
                if 'behavior' in theme['types'] and dimension in theme['habit_dimensions']:
                    result.append({key: theme[key] for key in ('domain', 'entry_id', 'revision', 'section')})
    return result


def material_stats(evidence, *, as_of):
    """Source coverage, not a percentage of actual behavior or automatic promotion."""
    current = date.fromisoformat(as_of)
    rows = {row['id']: row for row in evidence}
    dates = sorted({row['active_date'] for row in rows.values() if row.get('active_date')})
    return {'source_count': len(rows), 'material_days': len(dates),
            'first_material_date': dates[0] if dates else '',
            'last_material_date': dates[-1] if dates else '',
            'windows': {str(days): {'material_days': len({value for value in dates
                if (current - timedelta(days=days - 1)).isoformat() <= value <= as_of})}
                for days in (7, 28, 90)},
            'count_scope': 'validated_recorded_material_not_actual_occurrences',
            'unobserved_days': 'unknown'}
