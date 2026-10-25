"""Optional generated classification cannot invalidate supported book prose.

Strict manual/persistent topic validation remains in topics.normalize. Here
only complete valid annotations are accepted; conflicting scopes stay unknown.
"""
from . import books
from .topics import normalize

_CODES = {
    '主题分类结构无效': 'invalid_shape',
    '主题分类栏目不存在或重复': 'invalid_section',
    '主题类型与领域需要文本列表': 'invalid_labels',
    '主题认识类型无效': 'invalid_types',
    '主题适用范围需要文本': 'invalid_context',
    '习惯材料维度需要行为主题及已知维度': 'invalid_dimensions',
}


def select(value, body):
    if not isinstance(value, list):
        return [], [{'annotation_number': 0, 'code': 'invalid_list'}]
    grouped, rejected = {}, []
    for index, item in enumerate(value):
        try:
            row = normalize([item], body)[0]
        except books.BookError as exc:
            rejected.append({'annotation_number': index, 'code': _CODES.get(str(exc), 'invalid_annotation')})
            continue
        grouped.setdefault(row['section'], []).append((index, row))
    accepted = []
    for group in grouped.values():
        first = group[0][1]
        if all(row == first for _, row in group):
            accepted.append(first)
        else:
            # Do not merge different conditions into a broader claim or choose
            # one by output order. Original candidate annotations remain cached.
            rejected.extend({'annotation_number': index, 'code': 'conflicting_section'}
                            for index, _ in group)
    return accepted, sorted(rejected, key=lambda item: item['annotation_number'])
