"""Time qualifiers in a derived brief; no independent habit or state authority."""
from datetime import date

PERIODS = ('longstanding', 'recent', 'unspecified')


def validate(point, sources):
    from .books import BookError
    value = point.get('temporal', {'period': 'unspecified'})
    if not isinstance(value, dict) or value.get('period') not in PERIODS:
        raise BookError('认知简版时间层无效')
    period = value['period']
    dates = {}
    for key in ('as_of', 'review_after'):
        raw = value.get(key, '')
        if not isinstance(raw, str):
            raise BookError('认知简版时间限定无效')
        if raw:
            try:
                if date.fromisoformat(raw).isoformat() != raw:
                    raise ValueError()
            except ValueError:
                raise BookError('认知简版时间限定无效') from None
            # Book edit times are not observation/behavior dates. Only the
            # current source body can supply a date for a derived claim.
            if not any(raw in source['body'] for source in sources):
                raise BookError('认知简版时间缺少书页正文来源')
        dates[key] = raw
    if period == 'recent' and not dates['as_of']:
        raise BookError('近期简版需要书页已有的观察截至日期')
    if period == 'recent':
        from .other_facets import split
        from .other_temporal import time_receipt
        sections = []
        for source in sources:
            overview, facets = split(source['body'])
            sections.extend([overview, *(text for _, text in facets)])
        receipts = [receipt for text in sections if (receipt := time_receipt(text))
                    and receipt['as_of'] == dates['as_of']]
        recent = [receipt for receipt in receipts if receipt['period'] == 'recent']
        # Legacy unqualified prose remains a semantic review matter. An
        # explicit finite receipt, however, cannot be promoted by copying its
        # observation date. Section titles/edit metadata do not supply one.
        legacy = [text for text in sections if time_receipt(text) is None]
        if receipts and not recent and not any(dates['as_of'] in text for text in legacy):
            raise BookError('有限认识的截至日期不能作为近期规律来源')
        if (recent and dates['review_after'] and not any(
                receipt['review_after'] == dates['review_after'] for receipt in recent)
                and not any(dates['as_of'] in text and dates['review_after'] in text for text in legacy)):
            raise BookError('近期简版复核日期不属于对应近期认识')
    if period != 'recent' and any(dates.values()):
        raise BookError('非近期简版不能附加变化日期')
    if dates['review_after'] and dates['review_after'] < dates['as_of']:
        raise BookError('认知简版复核日早于观察日期')
    return {'period': period, **dates}


def render(points, *, today=None, point_renderer=None):
    """Reads remain model-free; elapsed review time is not a behavioral change."""
    today = today or date.today()
    groups = []
    for period, title in (('longstanding', '长期认识'), ('recent', '近期认识'),
                          ('unspecified', '已记录认识（时效未明确）')):
        texts = []
        for point in points:
            temporal = point.get('temporal', {'period': 'unspecified'})
            if temporal['period'] != period:
                continue
            from .brief_selection import render as render_selection
            text = (point_renderer or render_selection)(point)
            if period == 'recent':
                qualifier = f"观察截至 {temporal['as_of']}"
                review = temporal.get('review_after', '')
                if review:
                    qualifier += (f'；已到复核日 {review}，后续状态未确认'
                                  if today.isoformat() >= review else f'；复核日 {review}')
                text = f'（{qualifier}）{text}'
            texts.append(text)
        if texts:
            groups.append(title + '：\n' + '\n'.join(texts))
    return '\n\n'.join(groups)
