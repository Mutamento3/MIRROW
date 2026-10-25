"""Theme-sized search text with source-page context retained for reading."""
from .topics import project, sections
from .other_temporal import companion_titles, RECENT_SUFFIX


def recall_units(entry):
    parts = sections(entry['body'])
    overview = parts[0][1]
    titles = {title for title, _ in parts}
    result = []
    for topic in project(entry):
        title = topic['section']
        if title.endswith(RECENT_SUFFIX) and title.removesuffix(RECENT_SUFFIX) in titles:
            continue
        selected = companion_titles([title], parts[1:]) if title else {''}
        text = [overview] if title and overview else []
        text += [(f'## {section}\n' if section else '') + body
                 for section, body in parts if section in selected and body]
        result.append({**topic, 'context_body': '\n\n'.join(text),
                       'search_body': '\n\n'.join((f'## {section}\n' if section else '') + body
                           for section, body in parts if section in selected and body)})
    return result
