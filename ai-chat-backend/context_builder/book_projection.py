"""Displayed self-book prose and its request-local source coverage."""
from collections.abc import Mapping


class SelfBookText(str):
    """Legacy string API; source receipts never enter provider text or storage."""
    def __new__(cls, text, sources=()):
        value = super().__new__(cls, text)
        value.sources = tuple(sources)
        return value


def with_interest_overview(entries, related=()):
    """Active AI interest/tendency pages, independent of recall ranking.

    Metadata selects the declared book category/topic; prose is never scanned
    for semantic guesses. Related pages remain available after the overview.
    """
    def field(entry, key, default=''):
        return entry.get(key, default) if isinstance(entry, Mapping) else getattr(entry, key, default)
    def is_interest(entry):
        annotations = [topic for topic in (field(entry, 'topics', []) or []) if isinstance(topic, Mapping)]
        if annotations:
            return any('interest' in (topic.get('types') or []) for topic in annotations)
        return (field(entry, 'category') in {'兴趣', '倾向与偏好'}
                or 'interest' in (field(entry, 'topics', []) or []))
    interests = [entry for entry in entries
                 if field(entry, 'subject_id', 'k') == 'k'
                 and field(entry, 'state', 'active') == 'active'
                 and is_interest(entry)]
    result, seen = [], set()
    for entry in [*interests, *related]:
        identity = field(entry, 'entry_id') or field(entry, 'name')
        if identity not in seen:
            result.append(entry)
            seen.add(identity)
    return result


def render_self_book(entries, *, top_k=3, cap=0, separator='\n\n'):
    parts, sources = [], []
    for entry in entries[:top_k]:
        def field(key, default=''):
            return entry.get(key, default) if isinstance(entry, Mapping) else getattr(entry, key, default)
        name, body = str(field('name')), str(field('body') or '')
        if cap and cap > 0:
            body = body[:cap]
        metadata = '；'.join(filter(None, (
            'AI 的既有主观认识',
            '修订于 ' + str(field('updated_at')) if field('updated_at') else '',
            '来源：' + str(field('source')) if field('source') else '',
        )))
        parts.append(f'### {name}\n[{metadata}]\n{body}')
        # Unknown/manual strings remain fully available, but cannot establish
        # version-bound coverage merely by resembling a book's prose.
        identity = {key: field(key) for key in ('entry_id', 'revision', 'subject_id', 'basis')}
        if (body and all(isinstance(value, str) and value for value in identity.values())
                and field('domain', 'self') == 'self'):
            sources.append(dict(domain='self', name=name, body=body, **identity))
    text = '## 自我书\n' + separator.join(parts) if parts else ''
    return SelfBookText(text, sources)
