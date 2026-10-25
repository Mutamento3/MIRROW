"""Lossless textual units for source selection, not semantic segmentation."""
import re
from .books import BookError

# Keep delimiters, whitespace and Markdown in the source. Commas are not cuts;
# numbers with decimal dots and embedded Latin names remain intact.
_ENDS = re.compile(r'[。！？；!?;][”’」』"\']*|\r?\n')


def units(body):
    result, start = [], 0
    for match in _ENDS.finditer(body):
        end = match.end()
        result.append(body[start:end])
        start = end
    if start < len(body):
        result.append(body[start:])
    return result


def select(selection, indexed):
    if set(selection) != {'reference', 'first', 'last'}:
        raise BookError('认知简版原文编号选取无效')
    n, first, last = (selection[key] for key in ('reference', 'first', 'last'))
    if type(n) is not int or n not in indexed or type(first) is not int or type(last) is not int:
        raise BookError('认知简版原文编号来源无效')
    parts = units(indexed[n]['body'])
    if not 0 <= first <= last < len(parts):
        raise BookError('认知简版原文编号范围无效')
    return {'reference': n, 'quote': ''.join(parts[first:last + 1]).strip()}


def packet(row):
    """The complete body is transmitted exactly once, with stable ordinal IDs."""
    return {key: value for key, value in row.items() if key != 'body'} | {
        'fragments': [{'index': i, 'text': text} for i, text in enumerate(units(row['body']))]}
