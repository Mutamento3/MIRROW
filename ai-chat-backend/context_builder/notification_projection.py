"""Bounded LLM-facing facts derived from durable UI notifications.

The notification row remains UI-only.  Only explicitly registered event types
may produce a separate objective context fact; raw card copy and metadata never
become conversation messages.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

def _compact(value: Any, limit: int) -> str:
    text = re.sub('\\s+', ' ', str(value or '')).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + '…'
    return text

def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)][:6]

def _checkup_fact(message: Mapping[str, Any]) -> str:
    base = '[查岗事实] AI进行了一次查岗。'
    if str(message.get('active_date') or '') < '2026-09-26':
        return base
    from context_builder.text_utils import _redact_tool_fact
    raw = message.get('tool_calls') or message.get('toolCalls')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = []
    rows = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if len(rows) > 12:
        return base + '来源回执超过本次投影上限，需查看原始查岗记录。'
    facts = []
    for item in rows:
        status = str(item.get('status') or '')
        if status not in {'success', 'partial', 'failure', 'skipped'}:
            continue
        source = _redact_tool_fact(str(item.get('description') or item.get('tool') or '感知来源'))
        label = {'success': '已取得', 'partial': '仅部分取得', 'failure': '失败', 'skipped': '未尝试'}[status]
        result = _redact_tool_fact(str(item.get('result') or '').strip())
        error = _redact_tool_fact(str(item.get('error') or '').strip())
        detail = f"；来源回执{('节选' if len(result) >= 500 else '')}：{result}" if result else f'；原因：{error}' if error else ''
        facts.append(f'{source}：{label}{detail}')
    if not facts:
        return base + '本次没有可核验的感知回执，不能判断人类伙伴当时的活动。'
    return base + '各来源回执分别记录，视觉分析可能有误：' + '；'.join(facts) + '。'

def _ambient_time_note(event: Mapping[str, Any]) -> str:
    try:
        occurred = datetime.fromisoformat(str(event.get('occurred_start') or ''))
        learned = datetime.fromisoformat(str(event.get('learned_at') or ''))
        if occurred.tzinfo is None or learned.tzinfo is None:
            return ''
        if (learned - occurred).total_seconds() < 300:
            return ''
        local = timezone(timedelta(hours=8))
        occurred_local, learned_local = (occurred.astimezone(local), learned.astimezone(local))
        learned_format = '%m月%d日 %H:%M' if occurred_local.date() != learned_local.date() else '%H:%M'
        return f'声音发生于{occurred_local:%m月%d日 %H:%M}，AI于{learned_local.strftime(learned_format)}获知。'
    except (TypeError, ValueError):
        return ''

def ambient_event_brief(event: Mapping[str, Any]) -> str:
    """One short, uncertainty-preserving event fact shared by storage and context."""
    summary = _compact(event.get('summary'), 110)
    reason = _compact(event.get('participation_reason'), 60)
    unsupported_actor = '(?:AI|人类伙伴)(?:在|正|指示|说|处理|执行|使用|通过|确认|提到|谈到|要求|询问|操作|重启)'
    if re.search(unsupported_actor, summary):
        summary = '片段含具体谈话内容，无法可靠确认说话人和行动者'
    if re.search(unsupported_actor, reason):
        reason = '这段内容可能有需要确认的后续'
    backgrounds = event.get('device_background') if isinstance(event.get('device_background'), list) else []
    media = any((isinstance(item, dict) and item.get('media_playback_active') for item in backgrounds))
    call = any((isinstance(item, dict) and (item.get('kind') in {'cellular_call', 'voip_or_video_call'} or item.get('audio_mode') in {'in_call', 'in_communication'}) for item in backgrounds))
    source_note = '采集时设备处于通话且有媒体播放，音源未确认' if call and media else '采集时设备处于通话状态，近端与远端说话人未区分' if call else '采集时设备有媒体播放，音源未确认' if media else ''
    speaker = event.get('speaker') if isinstance(event.get('speaker'), dict) else {}
    speaker_note = f"声音与{_compact(speaker.get('name'), 30)}登记声纹相似，归属为候选、非逐句确认。" if speaker.get('basis') == 'local_voiceprint_candidate' and speaker.get('name') else '说话人未确认。'
    if speaker.get('entity_id') == 'unknown':
        observations = event.get('speaker_observations') or []
        names = []
        for observation in observations if isinstance(observations, list) else []:
            part = observation.get('speaker', {}) if isinstance(observation, dict) else {}
            for candidate in part.get('window_candidates', []) if isinstance(part, dict) else []:
                if isinstance(candidate, dict) and candidate.get('basis') == 'local_voiceprint_candidate' and candidate.get('name'):
                    name = _compact(candidate['name'], 30)
                    if name not in names:
                        names.append(name)
        if names:
            speaker_note = f"部分声音与{'、'.join(names[:3])}登记声纹相似，其余归属及逐句发言未确认。"
    return '[环境观察事件] AI听到一段环境声音：' + (summary or '内容尚无可靠摘要') + '。' + speaker_note + _ambient_time_note(event) + (f'{source_note}。' if source_note else '') + (f'联系缘由：{reason}。' if reason else '')

def _ambient_fact(message: Mapping[str, Any]) -> str:
    for item in _items(message.get('tool_calls') or message.get('toolCalls')):
        extra = item.get('extra_data') if isinstance(item.get('extra_data'), dict) else {}
        event = extra.get('ambient_event') if isinstance(extra.get('ambient_event'), dict) else None
        if not event:
            continue
        return ambient_event_brief(event)
    return ''

def build_notification_context_fact(message: Mapping[str, Any] | Any) -> str:
    """Return an objective projection for an allow-listed notification type."""
    if not isinstance(message, Mapping):
        return ''
    event_type = str(message.get('event_type') or message.get('eventType') or '')
    if event_type == 'workshop_event':
        from workshop.context import event_fact
        return event_fact(message)
    if event_type == 'social_gift':
        from context_builder.text_utils import _redact_tool_fact
        content = str(message.get('content') or '').strip()
        return '[共域赠礼事实] ' + _redact_tool_fact(content) if content else ''
    if event_type == 'checkup_observation':
        return _checkup_fact(message)
    if event_type == 'ambient_listening_observation':
        return _ambient_fact(message)
    if event_type in {'wish_board_update', 'social_feed_update'}:
        from notification_service import NOTIFICATION_CONTENT
        from context_builder.text_utils import _redact_tool_fact
        content = str(message.get('content') or '').strip()
        if not content or content == NOTIFICATION_CONTENT.get(event_type):
            return ''
        label = '许愿板事件' if event_type == 'wish_board_update' else '共域事件'
        return f'[{label}] {_redact_tool_fact(content)}'
    if event_type == 'band_action':
        from miband.history import action_fact
        from context_builder.text_utils import _redact_tool_fact
        facts = [action_fact(item) for item in _items(message.get('tool_calls') or message.get('toolCalls'))]
        facts = [fact for fact in facts if fact]
        return '[手环行为记录] ' + _redact_tool_fact('；'.join(facts)) if facts else ''
    return ''

def project_notification_message(message: Mapping[str, Any] | Any) -> Optional[dict[str, Any]]:
    """Convert an allow-listed notification to a system fact, or exclude it."""
    fact = build_notification_context_fact(message)
    if not fact:
        return None
    projected = dict(message)
    projected['role'] = 'system'
    projected['content'] = fact
    projected['tool_summary'] = None
    projected['tool_calls'] = None
    projected['notification_projection'] = True
    return projected
__all__ = ['ambient_event_brief', 'build_notification_context_fact', 'project_notification_message']
