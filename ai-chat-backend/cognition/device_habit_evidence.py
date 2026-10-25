"""Producer-verified device measurements join the existing other-book review.

Device time is not a statement, an activity category, or a mood judgment.
Only the actually observed local entity receives these evidence sources.
"""
import hashlib
import json
import logging
import sqlite3

from . import books

logger = logging.getLogger(__name__)


def _dates(source, before, limit):
    """Page the producer's 30-row read API; zero means complete available history."""
    selected, cursor = [], before
    while True:
        remaining = 30 if not limit else min(30, limit - len(selected))
        if remaining <= 0:
            break
        page = source.list_dates(before=cursor, limit=remaining)
        if not page:
            break
        if any(day >= cursor for day in page) or len(set(page)) != len(page):
            raise ValueError('invalid_device_date_page')
        selected.extend(page)
        cursor = min(page)
        if len(page) < remaining:
            break
    return selected


def leads(source_date, *, device_source=None, review_days=5, window_days=30):
    from habit_radar.sentinel_facts import SentinelDailySource
    if device_source is None:
        path = books.ROOT / "silicon_perception/data/silicon_perception.db"
        if not path.is_file():
            return []
        device_source = SentinelDailySource(path)
    try:
        if review_days < 1 or window_days < 0:
            raise ValueError('invalid_device_review_window')
        rows = [device_source.read_date(day) for day in _dates(device_source, source_date, window_days)]
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.warning("Device habit evidence unavailable: %s", type(exc).__name__)
        return []
    result = []
    for field in ("pc_active_min", "screen_time_total_min"):
        evidence = []
        for row in rows:
            if row is None:
                continue
            contract = row.field_evidence.get(field)
            if not contract or not contract.get("day_closed"):
                continue
            # Sparse PC sampling is only a lower observation, not a daily total
            # suitable for schedule comparison. Phone intervals always retain
            # their explicitly unknown collection coverage.
            if field == "pc_active_min" and contract.get("coverage_fraction", 0) < 0.75:
                continue
            value = row.values[field]
            if field == "pc_active_min":
                text = (f"人类伙伴的设备记录：{row.summary_date_key}（上海自然日），"
                        f"已采样电脑活跃 {value} 分钟；采样覆盖率 {contract['coverage_fraction']:.1%}。"
                        "用途和未采样时段未知，不等于工作时长或全天总量。")
            else:
                text = (f"人类伙伴的设备记录：{row.summary_date_key}（上海自然日），"
                        f"已记录手机使用区间合计 {value} 分钟；采集覆盖未知。"
                        "具体应用用途、未记录时段和未使用情况未知。")
            digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
            evidence.append({"id": f"device:{row.summary_date_key}:{field}:{digest}",
                "speaker": "device:human", "source_kind": "sentinel_day_fact", "text": text,
                "active_date": row.summary_date_key, "timestamp": contract["computed_at"]})
        if len({row["active_date"] for row in evidence}) < review_days:
            continue
        result.append({"subject_id": "human", "entity_name": "人类伙伴", "source_date": source_date,
                       "text": f"{field} 有多日设备观测，可与交互原话联合复核；设备指标本身不是习惯结论。",
                       'device_field': field, 'evidence': evidence})
    return result
