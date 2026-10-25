"""Frozen source clocks and one stable time-attribution contract."""
from datetime import datetime
from zoneinfo import ZoneInfo
from time_utils import TIMEZONE

TIME_SOURCE_NOTE = (
    '历史材料中的今天、昨天、明天以原记录的发生日期为锚点；活跃日可跨午夜，'
    '具体发生日期以各条时间为准。旧愿望、计划和主观感受不等于当前已发生的事实。'
)


def local_source_clock(value, *, seconds=False):
    """Never anchor an old record to reading time or invent a missing clock."""
    try:
        clock = datetime.fromisoformat(str(value or '').strip().replace('Z', '+00:00'))
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=ZoneInfo(TIMEZONE))
        return clock.astimezone(ZoneInfo(TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S' if seconds else '%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return ''


def source_range(start, end):
    first, last = local_source_clock(start), local_source_clock(end)
    if not first:
        return last or '时间未记录'
    if not last or last == first:
        return first
    return first + '—' + (last[11:] if first[:10] == last[:10] else last)
