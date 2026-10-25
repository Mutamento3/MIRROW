"""Reference repeated self prose only when this request displays its full source."""
from .brief_selection import render


def render_with_coverage(point, *, subject_id, sources, covered):
    selection = point.get('selection') or {}
    reference = selection.get('reference')
    text = point.get('text')
    source = sources.get(reference)
    if (subject_id == 'k' and point.get('source_voice') == 'self'
            and source and source.get('domain') == 'self'
            and source.get('subject_id') == subject_id
            and point.get('references') == [reference]
            and text and selection.get('quote') == text):
        keys = ('domain', 'entry_id', 'revision', 'subject_id', 'basis', 'name')
        for displayed in covered:
            if (all(source.get(key) and source.get(key) == displayed.get(key) for key in keys)
                    and text in displayed['body']):
                return f'你的自我认识（同源完整原述见本次自我书「{source["name"]}」）'
    return render(point)
