"""Category inheritance and Beijing schedules, independent of prompt recipes.

An absent category policy preserves the old family/node chains. Merely opening
settings never migrates effective routing or writes credentials.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
CATEGORIES = {'main': '主大脑', 'high': '高缓存命中率', 'low': '低缓存命中率'}
BEIJING = timezone(timedelta(hours=8))
MAIN_ROUTES = {'main_chat'}
EXPRESSION_ROUTES = {'wander_message', 'sentinel_push', 'workshop_expression', 'workshop_k_visit'}
EXPRESSION_ROUTES |= {'home_board_autonomous_reply'}

class TimeWindow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: str = Field(pattern='^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    end: str = Field(pattern='^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')

    def contains(self, now: datetime) -> bool:
        value = now.strftime('%H:%M')
        return self.start <= value < self.end if self.start < self.end else value >= self.start or value < self.end

class CategoryPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    peak_enabled: bool = False
    constant: list[str] = Field(default_factory=list, max_length=32)
    peak: list[str] = Field(default_factory=list, max_length=32)
    valley: list[str] | None = Field(default=None, max_length=32)
    schedule: Literal['official', 'custom'] = 'official'
    windows: list[TimeWindow] = Field(default_factory=lambda: [TimeWindow(start='09:00', end='12:00'), TimeWindow(start='14:00', end='18:00')], max_length=12)
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], max_length=7)
    holiday_dates: list[str] = Field(default_factory=list, max_length=370)
    retry_rounds: int = Field(default=3, ge=1, le=3)
    retry_delay_seconds: int = Field(default=10, ge=1, le=300)
    expiry_seconds: int = Field(default=3600, ge=30, le=86400)

    @field_validator('weekdays')
    @classmethod
    def valid_weekdays(cls, values):
        if len(set(values)) != len(values) or any((n not in range(7) for n in values)):
            raise ValueError('无效星期配置')
        return values

    @field_validator('holiday_dates')
    @classmethod
    def valid_holidays(cls, values):
        from datetime import date
        for value in values:
            date.fromisoformat(value)
        return values

def route_role(route: str) -> str:
    return 'main' if route in MAIN_ROUTES else 'expression' if route in EXPRESSION_ROUTES else 'internal'

def supported_roles(model: dict) -> set[str]:
    explicit = model.get('roles')
    name = str(model.get('model', '')).casefold()
    roles = {'internal'}
    if 'deepseek' in name:
        if 'pro' in name:
            roles.add('main')
        if 'flash' in name:
            roles.add('expression')
    return roles & set(explicit) if explicit else roles

def category_for(route: str, assignments: dict | None=None) -> str:
    from .catalog import group_for
    family = group_for(route)
    if assignments and (route in assignments or family in assignments):
        return assignments.get(route, assignments.get(family))
    if route in MAIN_ROUTES:
        return 'main'
    return 'low'

def period_for(policy: CategoryPolicy, now: datetime | None=None) -> str:
    now = now or datetime.now(BEIJING)
    now = now.replace(tzinfo=BEIJING) if now.tzinfo is None else now.astimezone(BEIJING)
    if not policy.peak_enabled:
        return 'constant'
    if policy.schedule == 'official':
        from deepseek_tariff import is_peak, HOLIDAYS
        if now.year not in HOLIDAYS:
            return 'constant'
        peak = is_peak(now.isoformat())
        return 'constant' if peak is None else 'peak' if peak else 'valley'
    for window in policy.windows:
        if window.start == window.end:
            continue
        start_day = now.date()
        if window.start > window.end and now.strftime('%H:%M') < window.end:
            start_day -= timedelta(days=1)
        if start_day.weekday() in policy.weekdays and start_day.isoformat() not in policy.holiday_dates and window.contains(now):
            return 'peak'
    return 'valley'

def select_ids(config, route: str, now: datetime | None=None) -> tuple[list[str], dict]:
    from .catalog import group_for
    family = group_for(route)
    category = category_for(route, config.assignments)
    policy = config.policies.get(category)
    if route in config.overrides:
        return (list(config.overrides[route]), {'category': category, 'period': 'override', 'inherited': False})
    if policy is None:
        return (list(config.chains.get(family, [])), {'category': category, 'period': 'legacy', 'inherited': False})
    period = period_for(policy, now)
    chain = policy.constant if period == 'constant' else policy.peak if period == 'peak' else policy.constant if policy.valley is None else policy.valley
    return (list(chain), {'category': category, 'period': period, 'inherited': True})

def validate_policies(config, known: set[str]) -> None:
    from .catalog import GROUP_IDS, NODE_LABELS
    if not set(config.policies) <= set(CATEGORIES):
        raise ValueError('未知任务大类')
    if not set(config.assignments) <= GROUP_IDS | set(NODE_LABELS):
        raise ValueError('未知分类节点')
    if any((value not in CATEGORIES for value in config.assignments.values())):
        raise ValueError('未知任务大类')
    if config.assignments.get('main_chat', 'main') != 'main' or any((value == 'main' for key, value in config.assignments.items() if key != 'main_chat')):
        raise ValueError('主大脑分类仅用于主动主聊天')
    for category, policy in config.policies.items():
        stable = 'direct_pro' if category == 'main' else 'direct_flash'
        stable_source = next(((s, m) for s in config.stations for m in s.models if m.id == stable), None)
        if not stable_source or not stable_source[0].enabled or (not stable_source[1].enabled):
            raise ValueError('大类策略需要启用末尾官方稳定来源')
        for chain in [policy.constant, policy.peak, *([] if policy.valley is None else [policy.valley])]:
            if len(set(chain)) != len(chain) or not set(chain) <= known:
                raise ValueError('顺位包含重复或不存在的来源')
        if not policy.constant or (policy.peak_enabled and (not policy.peak)) or (policy.peak_enabled and policy.valley == []):
            raise ValueError('生效策略必须配置完整的常驻及峰谷顺位')
        if any((w.start == w.end for w in policy.windows)):
            raise ValueError('时段起止不能相同')
        if category == 'main':
            by_id = {m.id: (s, m) for s in config.stations for m in s.models}
            for key in set(policy.constant + policy.peak + (policy.valley or [])):
                station, model = by_id[key]
                if station.protocol != 'openai-chat' or not ('deepseek' in model.model.casefold() and 'pro' in model.model.casefold()) or 'main' not in supported_roles(model.model_dump()):
                    raise ValueError('主大脑线路需为 DS Pro 的 Chat 兼容来源')
    by_id = {m.id: (s, m) for s in config.stations for m in s.models}
    for key in config.overrides.get('main_chat', []):
        station, model = by_id[key]
        if station.protocol != 'openai-chat' or not ('deepseek' in model.model.casefold() and 'pro' in model.model.casefold()):
            raise ValueError('主聊天独立顺位需为 DS Pro 的 Chat 兼容来源')

def public_categories(config) -> list[dict]:
    from .catalog import public_groups
    groups = public_groups()
    return [{'id': key, 'label': label, 'groups': [{**g, 'nodes': nodes} for g in groups if (nodes := [n for n in g['nodes'] if category_for(n['id'], config.assignments) == key])]} for key, label in CATEGORIES.items()]
