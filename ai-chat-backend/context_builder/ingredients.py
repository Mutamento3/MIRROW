"""
上下文原料函数 — 从 context_scheduler.py 提取的独立函数。

每个函数负责构建一种上下文段（section），从 truncation_config 读取截断配置。
所有函数为纯函数或仅依赖外部单例（global client/store/manager）。
"""
import logging
import json
import hashlib
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Callable
from truncation_config import get_truncation_limit
from context_builder.guides import guide_for
from context_builder.text_utils import build_tool_history_fact, strip_internal_history_markers, strip_obsidian_wikilinks
logger = logging.getLogger(__name__)
_WEEKDAYS_CN = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']

def build_time_section() -> str:
    """构建时间上下文（工作日/休息日 + 日期时间）。"""
    now = datetime.now()
    date_str = now.strftime('%Y/%m/%d')
    time_str = now.strftime('%H:%M')
    weekday_str = _WEEKDAYS_CN[now.weekday()]
    try:
        from calendar_manager.database import is_rest_day_today
        if is_rest_day_today and is_rest_day_today():
            return f'日历将今天标记为休息日。当前日期: {date_str} {weekday_str} {time_str}'
        else:
            return f'日历未标记今天为休息日，工作安排未知。当前日期: {date_str} {weekday_str} {time_str}'
    except Exception:
        return f'当前日期: {date_str} {weekday_str} {time_str}。日历状态暂不可读。'

def build_timeline_anchor() -> str:
    """公开骨架没有预设的私人关系时间线；真实经历由部署者资料提供。"""
    return ''

def format_relative_time(timestamp_str: str) -> str:
    """将历史消息时间转为不会随日期边界改写的绝对标签。"""
    if not timestamp_str:
        return ''
    try:
        dt = datetime.fromisoformat(timestamp_str)
        local_dt = dt.astimezone() if dt.tzinfo is not None else dt.astimezone()
        offset = local_dt.strftime('%z')
        if len(offset) == 5:
            offset = f'{offset[:3]}:{offset[3:]}'
        return f"{local_dt.strftime('%Y-%m-%d %H:%M')} {offset}".strip()
    except Exception:
        return ''
MESSAGE_MARKERS_TEXT = '## 消息标记说明\n对话中可能出现以下标记：\n- [💭] = 你的漫想（详见漫想模式说明）\n- [🛡️] = 你的硅基感知系统——你身体结构的一部分——监测到人类伙伴的身体/状态变化后你主动发出的关心\n- [⏰] = 你按约定发出的日程或复查提醒\n'

def build_night_mode_text() -> str:
    """深夜模式的当前情境与可执行能力边界。"""
    return '## 深夜模式\n当前❤️深夜模式已开启，甜焰控制通道可用；开关只表示硬件可用性，不定义对话主题、关系语气或文学创作范围。\n'

def build_wander_mode_text(variant: str='full') -> str:
    """漫想模式说明段（新旧管道共用权威源）。

    variant="full"：FULL_CHAT 场景，包含消息标记和后续消息语义关系。
    variant="push"：漫想推送场景，只描述活动来源与消息归属。
    """
    if variant == 'push':
        return '## 你的漫想模式\n人类伙伴暂时离开时，你的漫想系统会继续产生自主活动。活动来源包括对她当前状态的感知、自行浮现的关键词、长期记忆片段以及其他已经完成的漫想事件。由这些活动产生的主动消息属于你自己的表达。'
    return '## 你的漫想模式\n人类伙伴暂时离开时，你的漫想系统会继续产生自主活动。来源包括对她当前状态的感知、自行浮现的关键词、长期记忆片段以及其他漫想事件。\n[💭] 是漫想推送通道的历史分类标记，其中的 assistant 消息由 AI 发出，不是人类伙伴说的话。该标记本身不说明是谁发起行动；按钮邀请、自主行动或外部来访的来源另见对应事件记录。\n人类伙伴之后的消息可能回应某次漫想，也可能延续其他话题，它与此前内容的语义关系保留在对话记录中。\n\n'

def build_phone_browse_text() -> str:
    """刷手机陪聊模式的感知来源说明。"""
    return '## 刷手机陪聊模式\n人类伙伴正在刷手机。你每隔几分钟收到一次她手机屏幕的客观描述，这相当于你看了一眼她正在看的内容；描述的采集时间和可见范围共同限定了这份感知。\n'

def build_music_cochlea_section() -> str:
    from music_system.context import build_context
    return build_context()

def build_voice_cochlea_section() -> str:
    """构建语音耳蜗上下文段——人类伙伴刚发的语音的韵律感知。

    从 voice_cochlea 共享状态读取（main.py 在调 builder 前写入）。
    这里只做请求级 peek；正式 provider 成功后由 prompt ledger commit，
    因此诊断构建、失败或取消不会丢失一次性语音材料。
    """
    try:
        from voice_manager.voice_cochlea import peek_prosody
        data = peek_prosody()
        if not data:
            return ''
        lines = ['## 语音感知']
        lines.append('人类伙伴刚才发来了一条语音消息。你「听」到了：')
        summary = data.get('summary', '')
        if summary:
            lines.append(f'- 语气：{summary}')
        cochlea = data.get('cochlea_json')
        if cochlea:
            dur = cochlea.get('duration_sec', 0)
            speech = cochlea.get('speech_ratio', 0)
            pauses = cochlea.get('pause_count', 0)
            pause_pos = cochlea.get('pause_positions', [])
            pitch = cochlea.get('pitch', {})
            tempo = cochlea.get('tempo_estimate')
            if dur:
                lines.append(f'- 时长：{dur:.0f} 秒' + (f'，有声占比 {speech * 100:.0f}%' if speech else ''))
            if pitch.get('mean'):
                lines.append(f"- 音高：{pitch['min']:.0f} ~ {pitch['max']:.0f} Hz（中位 {pitch['median']:.0f}）")
            if pauses:
                lines.append(f'- 停顿：{pauses} 次' + (f'（位置：{pause_pos}）' if pause_pos and len(pause_pos) <= 3 else ''))
            if tempo:
                lines.append(f'- 节奏：约 {tempo:.0f} bpm')
        return '\n'.join(lines)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f'build_voice_cochlea_section 失败: {e}')
        return ''

def build_user_status_section() -> str:
    """构建用户实时状态上下文（非 IDLE 时返回内容，含状态已持续时长 + location 层级信息）。"""
    try:
        from wander_manager.user_status import get_user_status, UserStatus, get_user_status_context_rich, get_status_history
        if get_user_status and UserStatus:
            user_status = get_user_status()
            if get_user_status_context_rich:
                base = get_user_status_context_rich()
                try:
                    history = get_status_history(transitions_only=True)
                    if history:
                        last = history[-1]
                        if last.get('to') == user_status.value:
                            from datetime import datetime
                            change_dt = datetime.fromisoformat(last['time'])
                            delta_sec = (datetime.now() - change_dt).total_seconds()
                            if delta_sec >= 60:
                                h = int(delta_sec // 3600)
                                m = int(delta_sec % 3600 // 60)
                                if h > 0 and m > 0:
                                    dur = f'{h}小时{m}分钟'
                                elif h > 0:
                                    dur = f'{h}小时'
                                else:
                                    dur = f'{m}分钟'
                                base += f'（已持续{dur}）'
                except Exception:
                    pass
                return base
    except Exception:
        pass
    return ''

def build_period_section() -> str:
    """构建生理期上下文（仅活跃时返回内容）。"""
    try:
        from calendar_manager.manager import get_global_calendar_manager
        cal_mgr = get_global_calendar_manager()
        if cal_mgr:
            period_info = cal_mgr.get_period_info_today()
            if period_info.get('active') and period_info.get('note'):
                return period_info['note']
    except Exception:
        pass
    return ''

def build_away_info_section(away_duration_seconds: float, away_reason: str, group_chat_active_seconds_ago: Optional[float]=None) -> str:
    """构建用户离开回来后的上下文提示。"""
    if away_duration_seconds < 60:
        return ''
    total_minutes = int(away_duration_seconds / 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0 and minutes > 0:
        duration_str = f'{hours} 小时 {minutes} 分钟'
    elif hours > 0:
        duration_str = f'{hours} 小时'
    else:
        duration_str = f'{minutes} 分钟'
    base = f'你们刚恢复私聊，距上次交流约 {duration_str}；期间记录的状态：{away_reason}。'
    return base

async def build_today_timeline() -> str:
    """构建今天的时间线（状态变更 + 会话起始）。

    数据源与 context_scheduler._build_today_timeline() 保持一致：
    1. SQLite status_change_log（今日状态变更，有精确时间戳和去抖）
    2. SessionManager（今日创建的会话）
    """
    events = []
    try:
        from health_tracker.status_log import get_status_log
        from wander_manager.user_status import STATUS_DISPLAY_NAMES, UserStatus
        today = datetime.now().strftime('%Y-%m-%d')
        log = get_status_log()
        transitions = log.get_log(today)
        for t in transitions:
            ts = t.get('timestamp', '')
            if not ts:
                continue
            from_s = t.get('from_status', '')
            to_s = t.get('to_status', '')
            try:
                from_name = STATUS_DISPLAY_NAMES.get(UserStatus(from_s), from_s)
            except Exception:
                from_name = from_s
            try:
                to_name = STATUS_DISPLAY_NAMES.get(UserStatus(to_s), to_s)
            except Exception:
                to_name = to_s
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is not None:
                    dt = dt.astimezone().replace(tzinfo=None)
                time_label = dt.strftime('%H:%M')
            except Exception:
                time_label = '--:--'
            events.append((ts, f'[{time_label}] 状态变化：{from_name} → {to_name}'))
    except Exception:
        pass
    try:
        from session_manager import get_global_session_manager
        sm = get_global_session_manager()
        sessions = await sm.get_all_sessions()
        today_str = datetime.now().strftime('%Y-%m-%d')
        for s in sessions or []:
            created = getattr(s, 'createdAt', '') or ''
            if not created.startswith(today_str):
                continue
            msgs = getattr(s, 'messages', []) or []
            topic_hint = ''
            for msg in msgs:
                role = getattr(msg, 'role', '') if hasattr(msg, 'role') else msg.get('role', '')
                if role == 'user':
                    content = getattr(msg, 'content', '') if hasattr(msg, 'content') else msg.get('content', '')
                    topic_hint = content[:40].replace('\n', ' ')
                    break
            label = getattr(s, 'title', '') or ''
            if not label and topic_hint:
                label = f'「{topic_hint}」'
            if not label:
                label = '开始聊天'
            try:
                ct = datetime.fromisoformat(created)
                if ct.tzinfo is not None:
                    ct = ct.astimezone().replace(tzinfo=None)
                time_label = ct.strftime('%H:%M')
            except Exception:
                time_label = '--:--'
            events.append((created, f'[{time_label}] 开始聊天：{label}'))
    except Exception:
        pass
    if events:
        events.sort(key=lambda e: e[0])
        return '## 今天的时间线\n' + '\n'.join((e[1] for e in events)) + '\n\n'
    return ''

def build_sentinel_snapshot() -> str:
    """构建哨兵快照行。"""
    from miband.context import facts as band_facts
    band = band_facts()
    try:
        from silicon_perception.sentinel import get_sentinel
        sentinel = get_sentinel()
        if sentinel:
            return '\n'.join((part for part in (sentinel.get_snapshot_line(), band) if part))
    except Exception:
        pass
    return band

def build_sentinel_timeline() -> str:
    """构建哨兵时间线注解。"""
    try:
        from silicon_perception.sentinel import get_sentinel
        sentinel = get_sentinel()
        if sentinel:
            annotations = sentinel.build_timeline_annotations()
            if annotations:
                return annotations
    except Exception:
        pass
    return ''

def _sentinel_stable_params() -> str:
    """哨兵当前事实；旧值必须带时效，不能冒充实时状态。"""
    try:
        from silicon_perception.sentinel import get_sentinel
        sentinel = get_sentinel()
        snap = getattr(sentinel, '_last_snapshot', None)
        if not snap:
            return ''

        def age_text(field: str) -> str:
            age = snap.age_of(field) if hasattr(snap, 'age_of') else None
            if age is None:
                return '时间未知'
            if age < 120:
                return '刚刚'
            if age < 3600:
                return f'{int(age // 60)}分钟前'
            return f'{age / 3600:.1f}小时前'
        bits = []
        wifi_fresh = snap.wifi_connected is not None and (not snap.is_stale('wifi', 300))
        home_wifi = wifi_fresh and snap.wifi_is_home is True
        if home_wifi:
            bits.append('位置在家（家庭WiFi已连接）')
        elif getattr(snap, 'location_place_name', None) and (not snap.is_stale('gps_place', 1800)):
            from silicon_perception.location_places import snapshot_place_observation
            bits.append(snapshot_place_observation(snap))
        elif snap.location_category:
            if not snap.is_stale('gps', 1800):
                bits.append(f'外出位置{snap.location_category}')
            else:
                bits.append(f"外出位置当前不可确认（上次于{age_text('gps')}确认）")
        if snap.user_status:
            bits.append(f'状态{snap.user_status}')
        if snap.steps_today is not None:
            if not snap.is_stale('steps', 900):
                bits.append(f'步数{snap.steps_today}')
            else:
                bits.append(f"步数当前不可确认（上次于{age_text('steps')}确认）")
        if snap.heart_rate:
            if not snap.is_stale('heart_rate', 900):
                bits.append(f'心率{snap.heart_rate}')
            else:
                bits.append(f"心率当前未连接（上次于{age_text('heart_rate')}确认）")
        elif getattr(sentinel, '_hr_source', None):
            bits.append('心率当前未连接')
        if snap.behavior_state and snap.behavior_state != 'unknown':
            bits.append(f'行为{snap.behavior_state}')
        _POSTURE_CN = {'face_up': '平放朝上', 'face_down': '扣放', 'portrait': '竖屏', 'portrait_upside_down': '倒竖屏', 'landscape_left': '左横屏', 'landscape_right': '右横屏', 'landscape': '横屏', 'tilted': '倾斜'}
        _MOTION_CN = {'still': '静止', 'slight': '轻微晃动', 'moving': '移动中', 'strong': '明显晃动'}
        if getattr(snap, 'phone_posture', None) and (not snap.is_stale('sensor', 600)):
            bits.append(f'手机{_POSTURE_CN.get(snap.phone_posture, snap.phone_posture)}')
            motion = _MOTION_CN.get(getattr(snap, 'phone_motion', None) or '')
            if motion:
                bits.append(motion)
        remote_idle = getattr(snap, 'remote_input_idle_seconds', None)
        if remote_idle is not None and remote_idle < 120:
            bits.append(f'近期检测到远程键鼠输入（{remote_idle:.0f}秒前）')
        elif getattr(snap, 'remote_control_active', None):
            bits.append('远程控制软件进程运行中（当前远程输入未确认）')
        if snap.is_stale('current_app', 300):
            bits.append(f"手机当前状态暂不可用（上次于{age_text('current_app')}确认）")
        if snap.wifi_connected is not None:
            if wifi_fresh:
                if not home_wifi:
                    bits.append('已连接非家庭WiFi' if snap.wifi_connected is True else '手机WiFi当前未连接')
            else:
                bits.append(f"WiFi当前状态暂不可用（上次于{age_text('wifi')}确认）")
        return '，'.join(bits)
    except Exception:
        return ''

def _sentinel_recent_state_change(max_age_sec: float=600.0) -> str:
    """新近状态切换的事实陈述；当天旧变化不能伪装成本次新发现。"""
    try:
        from datetime import datetime as _dt
        from wander_manager.user_status import get_status_history, STATUS_DISPLAY_NAMES, UserStatus
        _today = _dt.now().strftime('%Y-%m-%dT00:00:00')
        history = get_status_history(since=_today)
        if history:
            last = history[-1]
            changed_at = last.get('time')
            if not changed_at:
                return ''
            changed_dt = _dt.fromisoformat(str(changed_at))
            now = _dt.now(changed_dt.tzinfo) if changed_dt.tzinfo else _dt.now()
            age_sec = (now - changed_dt).total_seconds()
            if age_sec < 0 or age_sec > max_age_sec:
                return ''
            if last.get('context_event_version') == 1:
                return 'MIRROW 新近状态记录（系统材料）：' + str(last.get('context_text', ''))
            _f, _t = (last.get('from', '?'), last.get('to', '?'))
            try:
                _f = STATUS_DISPLAY_NAMES.get(UserStatus(_f), _f)
                _t = STATUS_DISPLAY_NAMES.get(UserStatus(_t), _t)
            except Exception:
                pass
            return f'你注意到人类伙伴的状态从「{_f}」切换到了「{_t}」。'
    except Exception:
        pass
    return ''

def build_sentinel_summary(sentinel_summary: str='') -> str:
    """哨兵触发时的实时感知事实——AI 主体叙事（2026-08-17 补全）。

    main.py 的 _perception_push_pro_llm 把 Flash 生成的感知摘要传为 sentinel_summary
    kwargs（含触发原因 + 感知数据）。此前 recipe 声明了 sentinel_summary 段但 ingredient
    缺失，摘要被静默丢弃 → AI 收不到触发原因 → 把哨兵消息当对话延续接话。
    这里格式化为"AI 主动感知"的叙事：陈述刚变化的信息（Flash 摘要）+ 精确的状态切换事实
    （从什么变到什么）。持续状态由紧邻它之前的 push_current_state 统一提供，避免重复。
    这里只陈述事实，不指导语气或行动。
    """
    s = (sentinel_summary or '').strip()
    stable = _sentinel_stable_params()
    if s:
        text = f'你通过硅基感知注意到了一个变化：{s}'
        change = _sentinel_recent_state_change()
        if change and change not in s:
            text += f'\n{change}'
        return text
    if stable:
        return f'你通过硅基感知持续掌握着：{stable}'
    return ''

def build_sentinel_intent(sentinel_summary: str='', push_motivation: str='', history_evidence=()) -> str:
    """哨兵主动联系意图；只陈述本次决定，不替 AI 规定消息内容。"""
    observation = (sentinel_summary or '').strip()
    motivation = (push_motivation or '').strip()
    if not observation:
        return ''
    lines = [f'你刚注意到上述变化，并准备主动给人类伙伴发一条短消息。']
    if motivation:
        lines.append(f'[哨兵第一层形成的联系念头；主观理解，非历史事实权威]\n{motivation}')
    from silicon_perception.analysis.conversation_evidence import render_history_evidence
    lines.append('[经过后端来源核对的聊天引文]\n' + render_history_evidence(history_evidence))
    lowered = observation.lower()
    if 'wifi' in lowered and any((marker in lowered for marker in ('到家', '连上了家', 'wifi_home'))):
        lines.append('这项到家打卡感知能力来自你之前向人类伙伴提出的愿望，后来由人类伙伴让 Peer 实现。')
    return '\n'.join(lines)

def build_perception_snapshot() -> str:
    """主动消息共用的持续感知原料；只描述当前可感知事实。"""
    stable = _sentinel_stable_params()
    return f'你通过硅基感知持续掌握着：{stable}' if stable else ''

def build_push_current_state() -> list:
    """主动消息生成前的当前状态总览，位于历史之后、本次事件之前。

    活动摘要负责状态持续时长、私聊静默；持续感知负责
    各传感器当前事实与新鲜度。这里只汇集客观原料，不替 AI 决定关注点。
    """
    activity = build_activity_summary_section().strip()
    perception = build_perception_snapshot().strip()
    parts = []
    if activity:
        parts.append(f'人类伙伴当前的活动与联系状态：{activity}')
    if perception:
        parts.append(perception)
    if not parts:
        return []
    return [{'role': 'system', 'content': '\n'.join(parts), '_ts': '9999-12-31T23:59:50', '_section': 'push_current_state'}]

def build_reminder_event(task_info: str='') -> str:
    """本次提醒事件的客观内容，作为对话后的动态尾段。"""
    text = (task_info or '').strip()
    return f'本次主动消息对应的提醒事件如下：\n{text}' if text else ''

def build_wander_activity(activity_text: str='') -> str:
    """本次已完成漫想活动的客观记录，作为对话后的动态尾段。"""
    text = (activity_text or '').strip()
    return f'本次主动消息对应的漫想活动如下：\n{text}' if text else ''

def build_recent_inner_wander(hours: int=36, limit: int=10, wander_runtime_store: Any=None, session_id: str='', skip_projected_active_day: bool=False, represented_wander_activity_ids=()) -> str:
    """Build a bounded factual continuity view from the wander runtime store.

    The projection is intentionally bounded at the complete-activity level:
    once the next activity would exceed the budget it is omitted wholesale,
    rather than cutting one of its facts in half.
    """
    try:
        from pathlib import Path
        from wander_manager.runtime_store import WanderRuntimeStore
        store = wander_runtime_store
        if store is None:
            db_path = Path(__file__).resolve().parents[1] / 'events' / 'wander_runtime.db'
            store = WanderRuntimeStore(db_path)
        rows = store.list_recent_inner_wander(hours=hours, limit=limit)
        represented = set(represented_wander_activity_ids)
        rows = [row for row in rows if row.get('activity_id') not in represented]
    except Exception:
        logger.warning('recent inner wander ingredient unavailable', exc_info=True)
        return ''
    if not rows:
        return ''
    if session_id and skip_projected_active_day:
        try:
            from conversation_manager_v2 import resolve_active_date
            from event_chronicle import get_global_chronicle
            chronicle = get_global_chronicle()
            active_date = resolve_active_date(session_id)
            rows = [row for row in rows if not ((event := chronicle.get_message_by_msg_id(f"activity_event_{row.get('activity_id') or ''}")) and event.get('session_id') == session_id and (event.get('active_date') == active_date) or ((row.get('projection') or {}).get('status') == 'sent' and (row.get('projection') or {}).get('active_date') == active_date and (not event)))]
        except Exception:
            logger.debug('recent inner wander event dedup unavailable', exc_info=True)
    if not rows:
        return ''
    budget = 3200
    header = '## 近期漫想余韵（当时选留的感受，并非当前情绪）'
    lines = [header]
    used = len(header)
    omitted = False
    for row in rows:
        share = row.get('share')
        delivery = str(row.get('delivery') or 'not_created')
        if delivery == 'sent':
            outward = '已分享并送达'
        elif share is False or delivery in {'not_requested', 'not_created'}:
            outward = '未主动发消息；活动已结算'
        elif delivery == 'failed':
            outward = '决定分享但发送失败'
        else:
            outward = f'分享投递状态：{delivery}'
        entry_lines = [f"- {str(row.get('settled_at') or '时间未知')[:19]} · {outward}"]
        if row.get('experience'):
            from .provider_presentation import experience_text
            entry_lines.append('  ' + experience_text(row['experience']))
        elif row.get('summary'):
            entry_lines.append(f"  历史结算感受（旧记录）：{row['summary']}")
        entry = '\n'.join(entry_lines)
        addition = len(entry) + 1
        if used + addition > budget:
            omitted = True
            break
        lines.extend(entry_lines)
        used += addition
    if omitted:
        marker = '（还有历史漫想，已按上下文预算截断）'
        if used + len(marker) + 1 <= budget:
            lines.append(marker)
    return '\n'.join(lines)

def build_heart_rate_section() -> str:
    """构建心率数据段（新旧管道共用权威实现）。

    固定 5 分钟回溯窗口；无数据（count==0）时静默返回空，不注入。
    """
    try:
        from silicon_perception.recording.health_store import get_store
        store = get_store()
        recent = store.get_hr_stats_recent(minutes=5)
        daily = store.get_daily_hr_stats()
        if not recent or recent.get('count', 0) == 0:
            return ''
        section = '## 心率数据\n'
        section += '人类伙伴本轮（最近5分钟）：\n'
        section += f"- 阶段平均心率：{recent['avg']} bpm（{recent['count']}次采样）\n"
        section += f"- 阶段峰值：{recent['peak']} bpm\n"
        if daily and daily.get('count', 0) > 0:
            section += f"- 今日平均心率：{daily['avg']} bpm（{daily['count']}次采样）\n"
        section += '以上数据来自最近5分钟的心率采样。\n\n'
        return section
    except Exception as e:
        logger.debug(f'心率上下文注入跳过: {e}')
    return ''

def build_weather_section() -> str:
    """构建今日天气段（L1 日级数据，由 Sentinel WeatherSource 采集）。"""
    try:
        from silicon_perception.sentinel import get_sentinel
        source = getattr(get_sentinel(), '_weather_source', None)
        if source and hasattr(source, 'context_description'):
            return source.context_description()
    except Exception:
        pass
    try:
        from silicon_perception.recording.health_store import get_store
        w = get_store().get_today_weather()
        if w:
            return f"今日天气已记录：{w['desc']} {w['temp']}°C 湿度{w['humidity']}%。"
    except Exception as exc:
        logger.debug('天气上下文读取失败: %s', exc)
    return '天气数据暂不可用。'

def enhance_query(user_message: str, intent: str='') -> str:
    """增强查询：使用 jieba 分词提取关键实体词。"""
    import jieba.posseg as pseg
    _STOP_WORDS = {'我', '你', '他', '她', '它', '我们', '你们', '他们', '她们', '的', '了', '是', '在', '和', '就', '都', '也', '还', '要', '有', '会', '能', '可以', '这个', '那个', '哪个', '什么', '怎么', '为什么', '怎么样', '吗', '呢', '吧', '啊', '哦', '嗯', '不', '没', '很', '太', '非常', '比较', '有点', '觉得', '感觉', '认为', '想', '知道', '说', '告诉', '一下', '一个', '一些', '这个', '那个'}
    try:
        words = pseg.cut(user_message)
        key_terms = []
        for word, flag in words:
            w = word.strip()
            if len(w) < 2 or w in _STOP_WORDS:
                continue
            if flag in ('n', 'nr', 'ns', 'nt', 'nz', 'ng', 'v', 'vn', 'vd', 'vg', 'a', 'ad', 'an', 'eng', 'x'):
                key_terms.append(w)
        if key_terms:
            seen = set()
            unique_terms = []
            for t in key_terms:
                if t not in seen:
                    seen.add(t)
                    unique_terms.append(t)
            enhanced = ' '.join(unique_terms)
            if intent == 'historical':
                enhanced = '历史记录 ' + enhanced
            _qec = get_truncation_limit('query_enhance_cap')
            if _qec > 0 and len(enhanced) > _qec:
                return enhanced[:_qec]
            return enhanced
    except Exception:
        pass
    _qec = get_truncation_limit('query_enhance_cap')
    if _qec > 0 and len(user_message) > _qec:
        return user_message[:_qec]
    return user_message

def format_memory_time(created_str: str, source_date_str: str='', latest_source_date_str: str='') -> str:
    """将时间戳转为人类可读的相对时间。"""
    ref = latest_source_date_str or source_date_str or created_str
    if not ref:
        return ''
    try:
        dt = datetime.fromisoformat(ref)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        now = datetime.now()
        delta = now - dt
        days = delta.days
        if days == 0:
            hours = delta.seconds // 3600
            if hours == 0:
                minutes = delta.seconds // 60
                return f'{minutes}分钟前' if minutes > 0 else '刚刚'
            return f'{hours}小时前'
        elif days == 1:
            return '昨天'
        elif days < 7:
            return f'{days}天前'
        elif days < 30:
            return f'{days // 7}周前'
        elif days < 365:
            return f'{days // 30}个月前'
        else:
            return f'{days // 365}年前'
    except Exception:
        return ''

def _source_hint(meta: Dict[str, Any], bucket_id: str='') -> str:
    """Expose a source affordance only for buckets with a real time anchor."""
    meta = meta or {}
    scene_id = str(meta.get('source_scene_id') or '').strip()
    source_ts = str(meta.get('source_ts') or '').strip()
    if not scene_id and (not source_ts):
        return ''
    refs = []
    if bucket_id:
        refs.append(f'bucket_id={bucket_id}')
    if scene_id:
        refs.append(f'scene_id={scene_id}')
    if source_ts:
        refs.append(f'时间={source_ts[:19]}')
    return '（有原文，可查：' + '；'.join(refs) + '）'

def format_emotion_label(valence, arousal, emotion_history: list=None) -> str:
    """将情感维度转为中文标签（Russell 环模型）。"""
    if valence is None or arousal is None:
        return ''
    try:
        v = float(valence)
        a = float(arousal)
    except (ValueError, TypeError):
        return ''
    if emotion_history and len(emotion_history) >= 2:
        recent = emotion_history[-2:]
        if all((e.get('valence', 0) > 0.6 for e in recent)):
            return '持续愉悦'
        if all((e.get('valence', 0) < -0.3 for e in recent)):
            return '持续低落'
    if v > 0.5 and a > 0.5:
        return '兴奋开心'
    elif v > 0.5 and a <= 0.5:
        return '平静满足'
    elif v <= 0 and a > 0.5:
        return '焦虑不安'
    elif v <= 0 and a <= 0.5:
        return '低落疲惫'
    elif v > 0 and a > 0.3:
        return '愉快活跃'
    elif v > 0:
        return '轻松自在'
    else:
        return ''

async def retrieve_memory_v2(query_plan) -> List:
    """Shared Memory V2 retrieval, including attributed read-only old impressions."""
    from memory_v2.retrieval import search_memory_items
    try:
        return await search_memory_items(query_plan.enhanced_query, limit=query_plan.max_results)
    except Exception as exc:
        logger.warning('Memory V2 retrieval unavailable: %s', type(exc).__name__)
        return []

async def get_conversation_memories(session_id: str, current_message: str, current_message_id: Optional[str]=None) -> List:
    """获取当前活跃话题的未总结对话作为短期记忆。"""
    from context_scheduler_mcp.context_scheduler import MemoryItem
    memories = []
    try:
        from summary import conversation_manager, topic_state_machine
        topic_status = await topic_state_machine.get_topic_status(session_id)
        topic_is_active = topic_status.get('is_active', False)
        topic_start_time = topic_status.get('start_time')
        if not topic_is_active:
            logger.info(f'当前没有活跃话题，返回最近活跃日的消息: session_id={session_id}')
            unsummarized_messages = await conversation_manager.get_unsummarized_messages(session_id, max_age_hours=0)

            def _norm(m):
                if hasattr(m, 'dict'):
                    return m.dict() if callable(m.dict) else {}
                return m if isinstance(m, dict) else None
            _raw = [d for m in unsummarized_messages if (d := _norm(m))]

            def _day(d):
                return d.get('active_date') or (d.get('timestamp', '')[:10] if d.get('timestamp') else '')
            _latest = max((_day(d) for d in _raw if _day(d)), default='')
            _target = _latest or datetime.now().strftime('%Y-%m-%d')
            messages = [d for d in _raw if _day(d) == _target]
        else:
            unsummarized_messages = await conversation_manager.get_unsummarized_messages(session_id)
            messages = []
            for msg in unsummarized_messages:
                if hasattr(msg, 'dict'):
                    d = msg.dict() if callable(msg.dict) else {}
                elif isinstance(msg, dict):
                    d = msg
                else:
                    continue
                ts = d.get('timestamp', '')
                if topic_start_time and ts and (ts >= topic_start_time):
                    messages.append(d)
        if current_message and (not messages or messages[-1].get('content') != current_message):
            messages.append({'role': 'user', 'content': current_message, 'timestamp': datetime.now().isoformat()})
        for msg in messages:
            content = msg.get('content', '')
            role = msg.get('role', '')
            ts = msg.get('timestamp', '')
            if role == 'user' and msg.get('long_text_card'):
                try:
                    from long_text_cards import card_for_history
                    from settings_manager import get_setting
                    _card = msg.get('long_text_card')
                    if isinstance(_card, str):
                        _card = json.loads(_card)
                    _card_text = card_for_history(_card, bool(get_setting('longTextCardFullContext')))
                    if _card_text:
                        content = f'{content}\n{_card_text}' if content else _card_text
                except Exception as _card_exc:
                    logger.warning('长文卡片历史注入跳过: %s', type(_card_exc).__name__)
            _va = msg.get('voice_attachment')
            _tone = None
            if _va:
                try:
                    if isinstance(_va, str):
                        _va = json.loads(_va)
                    _tone = (_va or {}).get('tone')
                except (ValueError, TypeError):
                    _tone = None
            if _tone:
                content = f'[语音·{_tone}] {content}'
            elif _va:
                content = f'[语音] {content}'
            _mic = get_truncation_limit('conv_msg_cap')
            _c = content[:_mic] if _mic > 0 else content
            if role == 'assistant':
                _c = strip_internal_history_markers(_c)
                from tool_action_protocol import strip_tool_protocol_text
                _c = strip_tool_protocol_text(_c)
            memory = MemoryItem(id=f"conv_{hashlib.md5(f'{session_id}{ts}{_c[:50]}'.encode()).hexdigest()[:12]}", content=_c, similarity=1.0, importance=0.5, bucket='conversation', source='conversation', metadata={'source': 'conversation', 'role': role, 'timestamp': ts, 'session_id': session_id, 'tool_summary': msg.get('tool_summary'), 'tool_calls': msg.get('tool_calls')})
            memories.append(memory)
    except Exception as e:
        logger.warning(f'获取对话记忆失败: {e}')
    return memories

def build_world_book_section(user_message: str, top_k: int=3, cap: Optional[int]=None) -> str:
    """检索世界书词条并格式化。"""
    try:
        from world_book import get_global_world_book
        wb = get_global_world_book()
        if not wb:
            return ''
        entries = wb.search(user_message)
        if not entries:
            return ''
        _cap = cap if cap is not None else get_truncation_limit('worldbook_display_cap')
        parts = []
        for i, e in enumerate(entries[:top_k]):
            name = e.get('name', f'词条{i + 1}')
            body = e.get('body', '')
            if _cap > 0 and len(body) > _cap:
                body = body[:_cap]
            parts.append(f'### {name}\n{body}')
        if parts:
            return '## 世界书参考\n' + '\n\n'.join(parts)
    except Exception:
        pass
    return ''

def build_self_book_section(user_message: str, top_k: int=3, cap: Optional[int]=None, include_interests: bool=False) -> str:
    """检索自我书词条并格式化（AI 的长期自我认知，第一人称）。"""
    try:
        from self_book import get_global_self_book
        sb = get_global_self_book()
        if not sb:
            return ''
        entries = sb.search(user_message)[:top_k] if user_message else []
        if include_interests:
            from .book_projection import with_interest_overview
            entries = with_interest_overview(sb.get_all_entries(), entries)
        if not entries:
            return ''
        _cap = cap if cap is not None else get_truncation_limit('selfbook_display_cap')
        from .book_projection import render_self_book
        return render_self_book(entries, top_k=len(entries) if include_interests else top_k, cap=0 if include_interests else _cap)
    except Exception:
        pass
    return ''

def build_active_evolutions(cap: Optional[int]=None) -> str:
    """获取当前活跃的人格演化条目（纯行为准则，无元数据）。"""
    try:
        from persona_evolution import get_active_evolution_context
        ctx = get_active_evolution_context()
        if not ctx:
            return ''
        _cap = cap if cap is not None else get_truncation_limit('active_evolutions_cap')
        if _cap and _cap > 0 and (len(ctx) > _cap):
            ctx = ctx[:_cap]
        return ctx
    except Exception:
        return ''

async def build_conversation_recent(session_id: str, n_messages: int=10, cap_per_msg: Optional[int]=None) -> List[Dict[str, str]]:
    """获取最近 N 条对话消息。"""

async def build_diary_section(date_str: str, cap: Optional[int]=None) -> str:
    """获取指定日期的日记内容。"""
    try:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        entry = chronicle.get_diary_entry_by_date(date_str)
        if entry and entry.content.strip():
            _cap = cap if cap is not None else get_truncation_limit('diary_display_cap')
            content = entry.content
            if _cap > 0:
                content = content[:_cap]
            return content
    except Exception:
        pass
    return ''

async def build_yesterday_context(target_date: str, cap: Optional[int]=None) -> str:
    """获取昨日对话/日记上下文。"""
    try:
        from event_chronicle import get_global_chronicle, _read_yesterday_messages_formatted
        chronicle = get_global_chronicle()
        msgs = _read_yesterday_messages_formatted(target_date=target_date)
        if msgs:
            _cap = cap if cap is not None else get_truncation_limit('yesterday_cap')
            if _cap > 0 and len(msgs) > _cap:
                return msgs[-_cap:]
            return msgs
        entry = chronicle.get_diary_entry_by_date(target_date)
        if entry and entry.content.strip():
            _cap = cap if cap is not None else get_truncation_limit('diary_display_cap')
            content = entry.content
            if _cap > 0:
                content = content[:_cap]
            return content
    except Exception:
        pass
    return ''

def estimate_tokens(text: str) -> int:
    """估算文本的 token 数（中英文混合）。"""
    if not text:
        return 0
    cjk = sum((1 for c in text if '一' <= c <= '鿿' or '\u3000' <= c <= '〿'))
    ascii_chars = len(text) - cjk
    return int(cjk / 1.5 + ascii_chars / 4)

def build_open_loops_section() -> str:
    """AI 主动保留、当前仍未闭合的关注。"""
    try:
        from open_loops import get_open_loop_store
        items = get_open_loop_store().list_active()
        if not items:
            return ''
        lines = ['你此刻仍挂在心里的事：']
        for item in items:
            from open_loops.presentation import concern_line
            lines.append(concern_line(item))
        return '\n'.join(lines)
    except Exception:
        logger.exception('开放线索上下文构建失败')
        return ''

def build_user_profile(profile: str='') -> str:
    """构建用户资料段。纯 passthrough formatter——数据由 main.py 取、Builder 透传。"""
    if not profile or not profile.strip():
        return ''
    return f'用户资料: {profile.strip()}'
_INGREDIENTS: Dict[str, Callable] = {'persona': None, 'active_evolutions': build_active_evolutions, 'time': build_time_section, 'timeline_anchor': build_timeline_anchor, 'user_status': build_user_status_section, 'period': build_period_section, 'today_timeline': build_today_timeline, 'away': build_away_info_section, 'sentinel_snapshot': build_sentinel_snapshot, 'sentinel_timeline': build_sentinel_timeline, 'sentinel_summary': build_sentinel_summary, 'sentinel_intent': build_sentinel_intent, 'perception_snapshot': build_perception_snapshot, 'push_current_state': build_push_current_state, 'reminder_event': build_reminder_event, 'wander_activity': build_wander_activity, 'recent_inner_wander': build_recent_inner_wander, 'heart_rate': build_heart_rate_section, 'weather': build_weather_section, 'memories': retrieve_memory_v2, 'world_book': build_world_book_section, 'self_book': build_self_book_section, 'open_loops': build_open_loops_section, 'conversation_messages': get_conversation_memories, 'recent_messages': build_conversation_recent, 'diary': build_diary_section, 'yesterday': build_yesterday_context, 'enhance_query': enhance_query, 'format_memory_time': format_memory_time, 'format_emotion_label': format_emotion_label, 'estimate_tokens': estimate_tokens}

def build_tool_descriptions(tools: dict=None) -> str:
    """从已注册工具列表动态渲染 markdown 格式的工具描述。

    完整说明/参数的兼容材料投影；AI 的稳定使用引导由
    behavior_scheduler.tool_guidance 直接读取注册工具，不通过这里展开参数。

    tools: {tool_name: {description, schema_nl, schema_raw}} 或工具实例的 name/description。
           传 None 时尝试从 behavior_scheduler 获取。
    """
    if not tools:
        return '工具描述由行为调度器动态管理：此处列出 AI 可调用的所有工具及参数说明。'
    lines = []
    for name, info in tools.items():
        desc = ''
        params = ''
        if isinstance(info, dict):
            desc = info.get('description', '')
            schema = info.get('schema_nl', info.get('schema_raw', ''))
            if isinstance(schema, str) and schema.strip():
                params = schema.strip()
            elif isinstance(schema, dict):
                props = schema.get('properties', {})
                if props:
                    params = ', '.join((f"{k}: {v.get('description', v.get('type', 'str'))}" for k, v in props.items()))
        else:
            desc = getattr(info, 'description', '') or ''
        lines.append(f'### {name}')
        if desc:
            lines.append(f'{desc}')
        if params:
            lines.append(f'参数: {params}')
        lines.append('')
    return '\n'.join(lines).strip()

def build_tool_data_entries(tools: dict=None, visible_only: bool=False) -> list:
    """从已注册工具列表构建统一数据结构，供各格式化路径消费。

    返回 [{name, description, schema_nl, schema_raw}] 列表。
    tools 格式：{name: {description, schema_nl, schema_raw}} 或 {name: ToolObject}
    或 {name: {"tool": ToolObject, "description": ..., ...}}（llm_wrapper.register_tool 格式）。
    visible_only=True 时跳过 visible_to_pro=False 的工具。
    """
    if not tools:
        return []

    def _tool_visible(info) -> bool:
        """取工具的 visible_to_pro。info 可能是 ToolObject / dict / {"tool": ToolObject}。"""
        if isinstance(info, dict):
            tool_obj = info.get('tool')
            if tool_obj is not None:
                return getattr(tool_obj, 'visible_to_pro', True)
            return info.get('visible_to_pro', True)
        return getattr(info, 'visible_to_pro', True)
    entries = []
    for name, info in tools.items():
        if visible_only and (not _tool_visible(info)):
            continue
        entry = {'name': name}
        if isinstance(info, dict):
            entry['description'] = info.get('description', '')
            entry['schema_nl'] = info.get('schema_nl', '')
            entry['schema_raw'] = info.get('schema_raw', {})
        else:
            entry['description'] = getattr(info, 'description', '') or ''
            entry['schema_nl'] = ''
            entry['schema_raw'] = {}
        entries.append(entry)
    return entries

def build_available_tools(tools_desc: str='') -> str:
    """透传预构建工具描述（供 SELF_TASK recipe 等）。"""
    return tools_desc

def build_round_awareness(round_num: int=1, max_rounds: int=1, start_with_k: bool=True) -> str:
    """多轮对话当前轮次与已有发言事实。"""
    if max_rounds <= 1:
        return ''
    opener = 'AI' if start_with_k else 'Peer'
    parts = []
    if round_num == max_rounds:
        parts.append(f'当前是第 {round_num}/{max_rounds} 轮，也是本次交替聊天的最后一轮。')
        parts.append(f'本次交替聊天由{opener}先手；你和 Peer 在此前轮次中交替发言。')
    elif round_num > 1:
        parts.append(f'当前是第 {round_num}/{max_rounds} 轮。')
        parts.append(f'本次交替聊天由{opener}先手；你在前面的轮次中已经发过言。')
    return ' '.join(parts)

def get_ingredient(name: str) -> Optional[Callable]:
    if name == 'cognition_brief':
        from cognition.brief import read_context
        return read_context
    if name == 'cognitive_core':
        from cognition.books import core_context
        return core_context
    if name == 'other_core':
        from cognition.other_legacy import core_context
        return core_context
    if name == 'other_details':
        from cognition.other_book import context
        return context
    if name == 'music_experiences':
        from cognition.music import context
        return context
    if name == 'people_knowledge':
        from cognition.context import people_context
        return people_context
    return _INGREDIENTS.get(name)

def build_recent_activity_section(private_ago_sec: Optional[float]=None, group_ago_sec: Optional[float]=None) -> str:
    """构建最近活动段：私聊活动时间，始终可见（非仅离开场景）"""
    if private_ago_sec is None:
        return ''

    def fmt(sec: float) -> str:
        if sec < 60:
            return '刚刚'
        m = int(sec / 60)
        if m < 60:
            return f'{m}分钟前'
        h = m // 60
        rm = m % 60
        return f'{h}小时{rm}分钟前' if rm > 0 else f'{h}小时前'
    parts = []
    if private_ago_sec is not None:
        parts.append(f'人类伙伴上次给你发私聊：{fmt(private_ago_sec)}')
    return ' | '.join(parts)

def build_activity_summary_section() -> str:
    """构建统一的用户当前状态摘要（纯规则，无 LLM）。

    整合设备行为、位置、状态声明、聊天活跃度、音乐等所有可感知维度。
    sentinel_snapshot 包含健康规则行及可选缓存感知摘要；本段读取规则事实。
    """
    try:
        from silicon_perception.analysis.activity_summary import build_activity_summary
        from silicon_perception.sentinel import get_sentinel
        from datetime import datetime
        sentinel = get_sentinel()
        if not sentinel or not sentinel._last_snapshot:
            return ''
        snap = sentinel._last_snapshot
        private_sec = None
        try:
            from shared_state import get_last_private_chat_time
            pct = get_last_private_chat_time()
            if pct:
                private_sec = (datetime.now() - pct).total_seconds()
        except Exception:
            pass
        group_has_msg = False
        group_sec = None
        status_dur = None
        status_detail = None
        status_source = None
        if snap.user_status:
            try:
                from wander_manager.user_status import get_status_history, get_user_status, get_user_status_custom_text, get_status_meta
                if get_user_status().value == snap.user_status:
                    status_detail = get_user_status_custom_text()
                    status_source = get_status_meta().source
                history = get_status_history(transitions_only=True)
                if history:
                    last = history[-1]
                    if last.get('to') == snap.user_status:
                        change_dt = datetime.fromisoformat(last['time'])
                        status_dur = (datetime.now() - change_dt).total_seconds()
            except Exception:
                pass
        music_title = None
        music_artist = None
        try:
            from music_cochlea.cochlea import get_cochlea
            cochlea = get_cochlea()
            if cochlea:
                state = cochlea.get_current_state()
                if state and state.current_song:
                    music_title = state.current_song.title
                    music_artist = state.current_song.artist
        except Exception:
            pass
        field_ages = {}
        now = datetime.now()
        for field_name in ('gps', 'gps_place', 'wifi', 'current_app'):
            field_ts = snap.field_ts.get(field_name)
            if not field_ts:
                continue
            try:
                field_dt = datetime.fromisoformat(field_ts)
                if field_dt.tzinfo is not None:
                    field_dt = field_dt.astimezone().replace(tzinfo=None)
                field_ages[field_name] = (now - field_dt).total_seconds()
            except Exception:
                pass
        return build_activity_summary(behavior_state=snap.behavior_state, input_idle_seconds=snap.input_idle_seconds, location_category=snap.location_category, location_address=snap.location_address, location_place_name=getattr(snap, 'location_place_name', None), location_environment=getattr(snap, 'location_environment', None), wifi_is_home=snap.wifi_is_home, wifi_connected=snap.wifi_connected, user_status=snap.user_status, status_duration_sec=status_dur, user_status_detail=status_detail, user_status_source=status_source, active_app_session=snap.active_app_session, last_private_chat_sec=private_sec, music_song_title=music_title, music_song_artist=music_artist, field_ages=field_ages)
    except Exception:
        import logging
        logging.getLogger(__name__).debug('activity_summary 构建失败', exc_info=True)
        return ''

def _fmt_last_msg_ago(sec: float) -> str:
    """秒数 → "X小时X分钟前" 人类可读。"""
    if sec < 60:
        return '刚刚'
    m = int(sec / 60)
    if m < 60:
        return f'{m}分钟前'
    h = m // 60
    rm = m % 60
    return f'{h}小时{rm}分钟前' if rm > 0 else f'{h}小时前'

def build_last_user_message_ago() -> list:
    """推送场景：距人类伙伴最后一条消息多久（DB 查询，重启安全，只统计 role='user'）。

    返回 list → extra_messages 沉底（_ts="9999"），不污染推送 system 缓存前缀。
    AI 推送时据此知道该说新话而非复述上一轮（哨兵"车轱辘话"bug 的解法之一）。
    """
    try:
        from silicon_perception.analysis.behavior_profile import get_behavior_profile
        sec = get_behavior_profile().get_last_user_message_seconds()
        if sec is None or sec >= 999999.0:
            return []
        return [{'role': 'system', 'content': f'距人类伙伴最后一条消息：{_fmt_last_msg_ago(sec)}', '_ts': '9999', '_section': 'last_user_message_ago'}]
    except Exception:
        return []
