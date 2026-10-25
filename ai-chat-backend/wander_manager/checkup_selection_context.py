"""Passive device facts for choosing senses; never polls or executes a tool."""

import json
import math
from datetime import datetime

from silicon_perception.collection.base import SOURCE_CONTRACTS
from time_utils import parse_ts


def _sample(snapshot, source, now):
    captured_at = (getattr(snapshot, 'field_ts', None) or {}).get(source)
    if not captured_at:
        return {'source': source, 'status': 'unknown', 'captured_at': None,
                'age_seconds': None}
    captured = parse_ts(captured_at)
    age = (parse_ts(now.isoformat()) - captured).total_seconds()
    valid_time = captured.year > datetime.min.year and age >= 0
    max_age = SOURCE_CONTRACTS[source]['nominal_max_age_seconds']
    return {'source': source,
            'status': ('fresh' if age <= max_age else 'stale') if valid_time else 'unknown',
            'captured_at': captured_at,
            'age_seconds': round(age, 1) if valid_time else None}


def _idle(value):
    return round(value, 1) if (isinstance(value, (int, float)) and not isinstance(value, bool)
                             and math.isfinite(value) and value >= 0) else None


def passive_selection_facts(snapshot, *, now):
    """Keep source time and local/remote input separate, including unknowns."""
    keyboard = _sample(snapshot, 'input_idle', now)
    origin_available = getattr(snapshot, 'input_origin_available', False) is True
    local = _idle(getattr(snapshot, 'physical_input_idle_seconds', None))
    if local is None:
        local = _idle(getattr(snapshot, 'input_idle_seconds', None))
    remote = _idle(getattr(snapshot, 'remote_input_idle_seconds', None))
    if not origin_available or keyboard['status'] == 'unknown':
        local = remote = None
    keyboard.update(input_origin_available=origin_available,
                    local_physical_idle_seconds_at_sample=local,
                    remote_input_idle_seconds_at_sample=remote)
    if local is None and remote is None:
        keyboard['status'] = 'unknown'

    phone = _sample(snapshot, 'current_app', now)
    screen_on = getattr(snapshot, 'mobile_screen_on', None)
    if not isinstance(screen_on, bool) or phone['status'] == 'unknown':
        screen_on = None
        phone['status'] = 'unknown'
    phone['screen_state_at_sample'] = ('亮屏' if screen_on else '熄屏') if screen_on is not None else '未知'
    return {'read_at': now.isoformat(), '电脑键鼠': keyboard, '手机屏幕': phone}


def passive_selection_context(*, now):
    from silicon_perception.sentinel import get_sentinel
    from context_builder.ingredients import build_activity_summary_section

    snapshot = getattr(get_sentinel(), '_last_snapshot', None)
    facts = passive_selection_facts(snapshot, now=now)
    facts['activity_summary'] = {
        'source': '与主聊天共用的 activity_summary 已有事实投影，非本次新采集',
        'text': build_activity_summary_section() or '当前没有可用的统一活动摘要',
    }
    return ('[感官选择前的被动设备事实]\n'
            '来源：哨兵已有非图像采样，不是本节点新执行的感官结果。'
            '键鼠空闲秒数截至各自采样时刻，本地物理输入与远程输入分别记录；'
            '手机亮屏或熄屏是设备状态，不代表本人正在操作或已经入睡。\n'
            + json.dumps(facts, ensure_ascii=False))
