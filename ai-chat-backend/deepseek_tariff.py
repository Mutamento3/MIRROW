"""Beijing peak calendar for the official tariff verified on 2026-10-08.

Pricing: https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
2026 holidays: https://www.beijing.gov.cn/so/topics/1100000088/holiday.html
Unknown holiday years deliberately have no peak-hour estimate.
"""
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
HOLIDAYS = {2026: ((1, 1, 3), (2, 15, 23), (4, 4, 6), (5, 1, 5),
                   (6, 19, 21), (9, 25, 27), (10, 1, 7))}


def is_peak(ts_str: str) -> bool | None:
    try:
        dt = datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None
    dt = dt.replace(tzinfo=BEIJING) if dt.tzinfo is None else dt.astimezone(BEIJING)
    if dt.weekday() >= 5 or not (9 <= dt.hour < 12 or 14 <= dt.hour < 18):
        return False
    if dt.year not in HOLIDAYS:
        return None
    return not any(dt.month == month and first <= dt.day <= last
                   for month, first, last in HOLIDAYS[dt.year])
