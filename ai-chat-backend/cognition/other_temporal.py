"""Time-qualified facets inside the existing other book, not a profile store."""
from datetime import date
import re

RECENT_SUFFIX = "·近期变化"
_STAMP = re.compile(r"^截至 (\d{4}-\d{2}-\d{2}) 的(?:近期观察|有限认识)(?:（复核日 (\d{4}-\d{2}-\d{2})）)?：")


def content_text(text):
    """Remove only the writer's deterministic time receipt for review coverage."""
    return _STAMP.sub('', text, count=1)


def time_receipt(text):
    """Read only the writer's explicit leading protocol, not natural-language intent."""
    match = _STAMP.match(text)
    if not match:
        return None
    return {'period': 'recent' if '近期观察' in match[0] else 'unspecified',
            'as_of': match[1], 'review_after': match[2] or ''}


def facet_title(facet):
    title = str(facet.get("title") or "").strip()
    period = facet.get("period", "unspecified")
    if period not in {"longstanding", "recent", "unspecified"}:
        from .books import BookError
        raise BookError("认识时间层无效")
    return title + RECENT_SUFFIX if period == "recent" and not title.endswith(RECENT_SUFFIX) else title


def qualify(facet, refs, *, retained_texts=()):
    title, text = facet_title(facet), str(facet.get("text") or "").strip()
    limited = facet.get('period') == 'unspecified'
    if not title.endswith(RECENT_SUFFIX) and not limited:
        return title, text
    # Structural moves carry the old observation receipt. A new processing
    # date is not a new observation, even when today's speech triggered tidy-up.
    retained = content_text(text)
    receipts = set()
    kind = '有限认识' if limited else '近期观察'
    for old in retained_texts:
        match = _STAMP.match(old)
        if match and kind in match[0] and retained in content_text(old):
            receipts.add(match[0])
        elif not match and limited and retained in old:
            # Unstamped legacy understanding has no observation receipt.
            # Tidying it today cannot create one from today's unrelated refs.
            receipts.add('')
    if receipts:
        from .books import BookError
        if len(receipts) != 1:
            raise BookError('保留认识有多个旧时间范围，需要分别整理')
        receipt = next(iter(receipts))
        match = _STAMP.match(receipt)
        if facet.get('review_after') and facet['review_after'] != (match[2] if match else None):
            raise BookError('原样整理不能改变旧认识复核日期')
        return title, receipt + retained
    dates = sorted({row.get("active_date", "")[:10] or row.get("timestamp", "")[:10] for row in refs})
    dates = [value for value in dates if value]
    if not dates:
        from .books import BookError
        raise BookError("有时效的认识需要带日期的来源")
    observed = date.fromisoformat(dates[-1]).isoformat()
    review = facet.get("review_after", "") or ""
    if review:
        from .books import BookError
        try:
            if date.fromisoformat(review).isoformat() != review or review < observed:
                raise ValueError()
        except (TypeError, ValueError):
            raise BookError("近期认识复核日期无效") from None
    stamp = f"截至 {observed} 的" + ('有限认识' if limited else '近期观察') + (f"（复核日 {review}）" if review else "") + "："
    return title, stamp + _STAMP.sub("", text)


def retained_facets(old_body, remove_facets):
    """Only explicitly removed columns can supply a structural time receipt."""
    from .other_facets import split
    return [text for title, text in split(old_body)[1] if title in remove_facets]


def project_text(text, *, today=None):
    match = _STAMP.match(text)
    if match and match[2] and (today or date.today()).isoformat() >= match[2]:
        return f"截至 {match[1]} 的阶段性认识（已到复核日）：" + text[match.end():]
    return text


def companion_titles(selected, facets):
    """A relevant change is read alongside its baseline, not in isolation."""
    titles = {title for title, _ in facets}
    result = set(selected)
    for title in selected:
        baseline = title.removesuffix(RECENT_SUFFIX)
        result.update({baseline, baseline + RECENT_SUFFIX} & titles)
    return result
