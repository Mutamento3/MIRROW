"""Bounded, read-only presentation of foreground and background AI feelings."""
from typing import Any, Callable

from .sidecar import FAMILIES

SHORT_LABEL_LIMIT = 16
BACKGROUND_LIMIT = 3
# This is the existing boundary between "轻微的" and "一些", not a new decay clock.
FOREGROUND_MIN_INTENSITY = .3


def foreground_feelings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank cannot turn weak or unrenewed prior-day feelings into current detail."""
    result = []
    for item in items:
        freshness = item.get('active_day_freshness', item.get('active_day_continuity'))
        if (freshness != 'earlier_active_day'
                and float(item.get('effective_intensity', 0) or 0) >= FOREGROUND_MIN_INTENSITY):
            result.append(item)
            if len(result) == 3:
                break
    return result


def resonance_origin(authorship, *, display=False):
    if authorship == 'human_explicit':
        return '你主动记录' if display else '人类伙伴主动记录'
    if authorship == 'human_correction':
        return '你手工纠正' if display else '人类伙伴手工纠正'
    return '近期表达中识别' if display else '近期表达中识别到'


def background_feelings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select from the complete source-validated list, before board truncation."""
    foreground = foreground_feelings(items)
    seen = {str(item.get('label') or '').strip() for item in foreground}
    seen.update(str(item.get('family') or '') for item in foreground)
    result = []
    for item in items:
        if item in foreground:
            continue
        label = str(item.get('label') or '').strip()
        if not label or len(label) > SHORT_LABEL_LIMIT:
            # Existing long reports are never guessed or rewritten. A known
            # family is a coarser source-backed description; complex is not.
            label = str(item.get('family') or '')
            if label not in FAMILIES or label == '复杂/未归类':
                continue
        if label in seen:
            continue
        seen.add(label)
        result.append({'label': label, 'effective_intensity': item.get('effective_intensity', 0),
                       'authorship': item.get('authorship', 'observer_inferred')})
        if len(result) == BACKGROUND_LIMIT:
            break
    return result


def background_sentence(state, *, clean, level_word, resonance=False, display=False):
    background = state.get('background_feelings')
    if background is None:
        background = background_feelings(list(state.get('components') or []))
    parts = []
    groups = {}
    for item in list(background)[:BACKGROUND_LIMIT]:
        label = clean(item.get('label') or '', SHORT_LABEL_LIMIT)
        if not label:
            continue
        level = level_word(float(item.get('effective_intensity', 0) or 0))
        origin = resonance_origin(item.get('authorship'), display=display) if resonance else ''
        groups.setdefault((origin, level), []).append(label)
    for (origin, level), labels in groups.items():
        prefix = ('心底还留着淡淡的' if level == '轻微的' else '背景里还留有' + level)
        if resonance:
            prefix = origin + '的背景余韵：' + ('淡淡的' if level == '轻微的' else level)
        parts.append(prefix + '、'.join(labels))
    return '；'.join(parts) + '。' if parts else ''


def render_self_context(state: dict[str, Any], *, clean: Callable, level_word: Callable, cap: int = 700) -> str:
    items = state.get('foreground_feelings')
    if items is None:
        items = foreground_feelings(list(state.get('components') or []))
    items = list(items)[:3]
    cap = max(100, int(cap))
    background_line = background_sentence(state, clean=clean, level_word=level_word)
    if not items and not background_line:
        return '[AI自身当前的情绪]\n目前没有新的持久化情绪投影。'
    lines = ['[AI自身当前的情绪]']
    details = []
    for index, item in enumerate(items):
        # Reserve space for a complete background sentence before reasons.
        label = clean(item.get('label') or '未命名感受', min(80, max(8, cap // 4)))
        level = level_word(float(item.get('effective_intensity', 0) or 0))
        lines.append(('当前主导：' if index == 0 else '仍有余留：') + level + label)
        age_text = item.get('continuity_text') or ''
        reason = clean(item.get('reason') or '', 120)
        details.append('；'.join(part for part in (age_text, reason) if part))
    suffix = '\n' + background_line if background_line else ''
    while len('\n'.join(lines) + suffix) > cap and len(lines) > 2:
        lines.pop()
        details.pop()
    if len('\n'.join(lines) + suffix) > cap:
        suffix = ''
    remaining = cap - len('\n'.join(lines) + suffix)
    for index, detail in enumerate(details):
        if detail and remaining >= 5:
            budget = min(len(detail) + 2, remaining // max(1, len(details) - index))
            if budget >= 5:
                text = detail if len(detail) + 2 <= budget else detail[:budget - 3] + '…'
                addition = '（' + text + '）'
                lines[index + 1] += addition
                remaining -= len(addition)
    return '\n'.join(lines) + suffix
