"""Read existing book themes through the bookshelf, without a second index."""
from . import books, topics


def read(*, subject_id=None, area=None, knowledge_type=None, topic_id=None,
         revision=None, offset=0, limit=10, detail=False):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise books.BookError('主题分页参数无效')
    if detail and not topic_id:
        raise books.BookError('展开认识需要先选择目录中的 topic_id')
    with books.LOCK:
        view = topics.directory(subject_id=subject_id, area=area, knowledge_type=knowledge_type)
        rows = view['topics']
        if topic_id:
            rows = [row for row in rows if row['topic_id'] == topic_id]
            if not rows:
                raise books.BookError('主题不存在或当前筛选范围内不可用，请重新读取目录')
            if revision and rows[0]['revision'] != revision:
                raise books.RevisionConflict('认识已修订，请重新读取当前目录')
        selected = rows[offset:offset + limit]
        result = {'source': view['source'], 'total': len(rows),
                  'unclassified_count': view['unclassified_count'],
                  'next_offset': offset + len(selected) if offset + len(selected) < len(rows) else None,
                  'topics': [{key: value for key, value in row.items() if key != 'body'} for row in selected]}
        if detail and selected:
            row = selected[0]
            entry = next(entry for entry in books.catalog(row['domain'])
                         if entry['entry_id'] == row['entry_id'])
            # A theme may depend on its overview, baseline, exceptions or later
            # qualifications. Return the current page, not an isolated claim.
            result['topics'][0]['body'] = row['body']
            result['entry'] = {key: entry.get(key) for key in
                              ('name', 'domain', 'entry_id', 'revision', 'subject_id',
                               'knower_id', 'kind', 'basis', 'body', 'topics')}
    return result
