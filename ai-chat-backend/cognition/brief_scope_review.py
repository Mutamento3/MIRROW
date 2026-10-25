"""Check source-bound scope review receipts, not natural-language semantics."""
from . import books


def review_units(text):
    """Lossless punctuation layout only; units do not classify assertions."""
    units, start = [], 0
    for index, char in enumerate(text):
        if char in '，；。！？\n' and text[start:index + 1].strip():
            units.append(text[start:index + 1])
            start = index + 1
    if start < len(text):
        # Keep even a whitespace suffix; the protocol covers the original text.
        if text[start:].strip() or not units:
            units.append(text[start:])
        else:
            units[-1] += text[start:]
    return units


def scope_approved_indices(raw, review_input, approved):
    """Every clause needs supported scope and real own-source quotes.

    The reviewer decides semantic scope. Code only enforces receipt completeness,
    exact quote provenance, and agreement between that decision and approval.
    One unsupported point does not remove independent points.
    """
    candidates = {item['index']: item for item in review_input['candidates']}
    checks = raw.get('scope_checks')
    if not isinstance(checks, list):
        raise books.BookError('认知简版复核缺少适用范围收据，保留上一版')
    by_index = {}
    for check in checks:
        if not isinstance(check, dict) or type(check.get('index')) is not int:
            raise books.BookError('认知简版适用范围收据结构无效')
        index = check['index']
        if index in by_index or index not in candidates or candidates[index]['kind'] != 'point':
            raise books.BookError('认知简版适用范围收据索引无效')
        by_index[index] = check
    accepted = set(approved)
    for index in approved:
        candidate = candidates[index]
        if candidate['kind'] != 'point':
            continue
        clauses = by_index.get(index, {}).get('clauses')
        units = review_units(candidate['text'])
        if (not isinstance(clauses, list) or not clauses
                or any(not isinstance(clause, dict) or not isinstance(clause.get('text'), str)
                       or not clause['text'].strip() for clause in clauses)
                or [clause['text'] for clause in clauses] != units):
            accepted.discard(index)
            continue
        sources = {row['n']: row for row in candidate['sources']}
        for clause in clauses:
            if not _supported_clause(clause, sources):
                accepted.discard(index)
                break
    return accepted


def _supported_clause(clause, sources):
    if (clause.get('support') != 'supported'
            or not isinstance(clause.get('relation'), str)
            or clause['relation'] not in {'same', 'narrower'}
            or not isinstance(clause.get('source_scope'), str) or not clause['source_scope'].strip()):
        return False
    evidence = clause.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        return False
    for quote in evidence:
        if not isinstance(quote, dict) or type(quote.get('reference')) is not int:
            return False
        row = sources.get(quote['reference'])
        text = quote.get('quote')
        if row is None or not isinstance(text, str) or not text.strip() or text not in row['body']:
            return False
    return any(clause['source_scope'] == quote['quote'] for quote in evidence)
