"""Objective device facts; no wake-up or behavior decisions."""
import json
from datetime import datetime
from .store import overview, TZ


def facts():
    data = overview()
    if not data['available']:
        return ''
    parts = ['小米手环7（手机蓝牙同步）']
    hr = data.get('heart_rate')
    if hr:
        ts = datetime.fromtimestamp(hr['measured_at'], TZ).strftime('%m-%d %H:%M')
        parts.append(f"心率{hr['value']}bpm，测于{ts}" + ('，已过期' if hr['stale'] else ''))
    steps = data.get('steps')
    if steps:
        parts.append(f"{steps['date']}已同步分钟步数合计{steps['value']}步（不是手机步数；可能未覆盖全天）")
    sleep = data.get('sleep')
    if sleep:
        parts.append(
            f"结束日期归属{sleep['date']}的设备睡眠分钟累计{sleep['total_sleep_min']}分钟；"
            "原生分期提示未经厂商算法校准，当前不据此区分深睡、浅睡或REM；"
            "这是历史记录，不等于当前入睡状态，也不同于人类伙伴声明睡眠"
        )
    return '；'.join(parts)


def query(action):
    data = overview()
    key = {'heart_rate_continuous': 'heart_rate', 'daily': None, 'all_health': None}.get(action, action)
    if key in ('spo2','stress','pai','respiratory_rate'):
        return None
    if not data.get('available') or (key and not data.get(key)):
        return None
    return json.dumps(data if key is None else {'source': data['source'], key: data[key]}, ensure_ascii=False)
