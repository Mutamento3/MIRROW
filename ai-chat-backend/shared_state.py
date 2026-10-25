"""
共享全局状态 — 从 main.py 提取的关键运行时状态
通过 getter/setter 提供稳定 API，消除 `from main import _xxx` 反向依赖

供以下模块使用:
- wander_manager (mobile_connected)
- silicon_perception (night_mode_active, mirrow_page_visible, last_user_message_time)
- Phase 2 提取的路由模块 (latest_session_id, latest_persona_prompt)
"""
import asyncio
from typing import Optional, Dict, Callable
_latest_session_id: Optional[str] = None
_latest_persona_prompt: Optional[str] = None

def get_latest_session_id() -> Optional[str]:
    return _latest_session_id

def get_active_session_id() -> str:
    """获取当前活跃会话 ID。_latest_session_id 优先，回退 sessions_index 的 current_session_id。

    后端重启后 _latest_session_id 为 None（内存变量），后台任务（哨兵/漫想/提醒）需要从
    sessions_index.json 恢复，否则它们的 conversation_messages（对话历史）会因 session_id 为空而返回空。
    """
    if _latest_session_id:
        return _latest_session_id
    try:
        import json as _json
        import os as _os
        _idx_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'sessions', 'sessions_index.json')
        if _os.path.exists(_idx_path):
            with open(_idx_path, 'r', encoding='utf-8') as _f:
                _idx = _json.load(_f)
            _sid = _idx.get('current_session_id') if isinstance(_idx, dict) else None
            if _sid:
                return _sid
    except Exception:
        pass
    return ''

def set_latest_session_id(sid: str):
    global _latest_session_id, _last_private_chat_time, _last_private_chat_time_resolved
    if sid and sid != _latest_session_id:
        _last_private_chat_time = None
        _last_private_chat_time_resolved = False
    _latest_session_id = sid

def get_latest_persona_prompt() -> Optional[str]:
    return _latest_persona_prompt

def set_latest_persona_prompt(prompt: str):
    global _latest_persona_prompt
    _latest_persona_prompt = prompt
_mirrow_page_visible: bool = False
_mirrow_page_last_seen: float = 0.0

def get_mirrow_page_visible() -> bool:
    return _mirrow_page_visible

def set_mirrow_page_visible(val: bool):
    global _mirrow_page_visible
    _mirrow_page_visible = val

def get_mirrow_page_last_seen() -> float:
    return _mirrow_page_last_seen

def set_mirrow_page_last_seen(val: float):
    global _mirrow_page_last_seen
    _mirrow_page_last_seen = val
_night_mode_active: bool = False

def get_night_mode_active() -> bool:
    return _night_mode_active

def set_night_mode_active(val: bool):
    if not isinstance(val, bool):
        raise TypeError('Night mode requires a boolean')
    global _night_mode_active
    _night_mode_active = val
_sentinel_last_tick: float = 0.0

def get_sentinel_last_tick() -> float:
    return _sentinel_last_tick

def set_sentinel_last_tick(val: float):
    global _sentinel_last_tick
    _sentinel_last_tick = val
_mobile_relay_callback: Optional[Callable] = None

def get_mobile_relay_callback() -> Optional[Callable]:
    return _mobile_relay_callback

def set_mobile_relay_callback(cb: Optional[Callable]):
    global _mobile_relay_callback
    _mobile_relay_callback = cb

async def dispatch_mobile_tool(request_id: str, tool: str, params: dict) -> dict:
    """Resolve the current relay at invocation time for long-lived sensory tools."""
    callback = get_mobile_relay_callback()
    if callback is None:
        raise RuntimeError('手机 WebSocket 未连接')
    return await callback(request_id, tool, params)
_latest_platform: str = ''

def get_latest_platform() -> str:
    return _latest_platform

def set_latest_platform(val: str):
    global _latest_platform
    _latest_platform = val
_music_mode_active: bool = False

def get_music_mode_active() -> bool:
    return _music_mode_active

def set_music_mode_active(val: bool):
    assert isinstance(val, bool), f'set_music_mode_active expects bool, got {type(val).__name__}'
    global _music_mode_active
    _music_mode_active = val
    try:
        from settings_manager import set_setting
        set_setting('music_mode_state', val)
    except Exception:
        pass
import time as _time
_last_user_message_time: float = 0.0

def get_last_user_message_time() -> float:
    return _last_user_message_time

def set_last_user_message_time(val: float):
    """记录用户最后一条消息时间（monotonic 时钟）。拒绝明显错误的 epoch 值。"""
    import logging
    global _last_user_message_time
    if val > 1000000000.0:
        logging.getLogger(__name__).warning('set_last_user_message_time 拒绝非 monotonic 时钟值 (val=%.1f, 疑似 time.time())，已忽略', val)
        return
    _last_user_message_time = val
from datetime import datetime as _dt
from typing import Optional as _Opt
_last_private_chat_time: _Opt[_dt] = None
_last_private_chat_time_resolved: bool = False
_last_group_chat_time: _Opt[_dt] = None

def set_last_private_chat_time(dt: _dt):
    global _last_private_chat_time, _last_private_chat_time_resolved
    _last_private_chat_time = dt
    _last_private_chat_time_resolved = True

def _restore_last_private_chat_time() -> _Opt[_dt]:
    """Read the last real private user message once after a process restart.

    ``last_reply_time.json`` belongs to the mode switch and describes the
    physical/activation clock.  It is deliberately not used here: a AI push,
    notification, or an old device-idle timestamp must never become evidence
    that A-Ning sent a private message.  The conversation chronicle is the
    authority for this fact.
    """
    try:
        session_id = get_active_session_id()
        if not session_id:
            return None
        from event_chronicle import get_global_chronicle
        from time_utils import TIMEZONE, parse_ts
        from zoneinfo import ZoneInfo
        rows = get_global_chronicle().get_messages_by_session_id(session_id, limit=200, desc=True) or []
        latest = None
        for row in rows:
            if not isinstance(row, dict) or str(row.get('role') or '') != 'user':
                continue
            if any((row.get(flag) for flag in ('is_wander', 'is_sentinel', 'is_reminder'))):
                continue
            if not str(row.get('content') or '').strip():
                continue
            stamp = row.get('timestamp')
            if not stamp:
                continue
            try:
                parsed = parse_ts(str(stamp)).astimezone(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
            except Exception:
                try:
                    parsed = _dt.fromisoformat(str(stamp))
                except Exception:
                    continue
            if latest is None or parsed > latest:
                latest = parsed
        return latest
    except Exception:
        return None

def get_last_private_chat_time(resolve_from_store: bool=True) -> _Opt[_dt]:
    """Return the last real private user-message time.

    The first read after restart performs one bounded chronicle lookup.  Later
    ticks are served from memory, while ``set_last_private_chat_time`` updates
    the cache only for an accepted private user message.
    """
    global _last_private_chat_time, _last_private_chat_time_resolved
    if resolve_from_store and (not _last_private_chat_time_resolved):
        _last_private_chat_time = _restore_last_private_chat_time()
        _last_private_chat_time_resolved = True
    return _last_private_chat_time

def set_last_group_chat_time(dt: _dt):
    global _last_group_chat_time
    _last_group_chat_time = dt

def get_last_group_chat_time() -> _Opt[_dt]:
    return _last_group_chat_time
_last_k_group_context: str = ''
_last_peer_group_context: str = ''
_mobile_ws_count: int = 0

def get_mobile_connected() -> bool:
    """手机是否有仍在租约内的 mobile WS。"""
    try:
        from mobile_relay import get_mobile_relay_registry
        return get_mobile_relay_registry().has_live_connection()
    except Exception:
        return _mobile_ws_count > 0

def incr_mobile_ws():
    """mobile WS 连接 +1"""
    global _mobile_ws_count
    _mobile_ws_count += 1

def decr_mobile_ws():
    """mobile WS 断开 -1，返回是否还有剩余连接"""
    global _mobile_ws_count
    if _mobile_ws_count > 0:
        _mobile_ws_count -= 1
    return _mobile_ws_count > 0

def set_mobile_connected(val: bool):
    """兼容旧调用：强行设置（不推荐，优先用 incr/decr）"""
    global _mobile_ws_count
    _mobile_ws_count = 1 if val else 0
_phone_browse_mode: bool = False
_phone_browse_paused: bool = True

def get_phone_browse_mode() -> bool:
    return _phone_browse_mode

def set_phone_browse_mode(val: bool):
    assert isinstance(val, bool), f'set_phone_browse_mode expects bool, got {type(val).__name__}'
    global _phone_browse_mode
    _phone_browse_mode = val

def get_phone_browse_paused() -> bool:
    return _phone_browse_paused

def set_phone_browse_paused(val: bool):
    assert isinstance(val, bool), f'set_phone_browse_paused expects bool, got {type(val).__name__}'
    global _phone_browse_paused
    _phone_browse_paused = val
_current_user_status: str = ''
_current_user_status_ts: Optional[str] = None
_current_status_meta: Optional[dict] = None

def get_current_user_status() -> str:
    return _current_user_status

def get_current_user_status_ts() -> Optional[str]:
    """上次 user_status 变更时间（供时效判断，防 stale 'out' 关掉心率）。"""
    return _current_user_status_ts

def set_current_user_status(val: str):
    global _current_user_status, _current_user_status_ts
    import datetime as _dt
    if val != _current_user_status:
        _current_user_status_ts = _dt.datetime.now().isoformat()
    _current_user_status = val

def get_current_status_meta() -> Optional[dict]:
    """获取当前 StatusMeta（dict 格式，供 sentinel 等消费者免轮询读取）。"""
    return _current_status_meta

def set_current_status_meta(meta_dict: dict):
    """设置当前 StatusMeta（由 user_status.set_user_status_combined / bridge 调用）。"""
    global _current_status_meta
    _current_status_meta = meta_dict
_current_gps_category: str = ''
_current_gps_updated_at: Optional[str] = None

def get_current_gps_category() -> str:
    return _current_gps_category

def get_current_gps_age_seconds() -> Optional[float]:
    """当前位置事实年龄（秒），None = 从未有数据。"""
    if not _current_gps_updated_at:
        return None
    import datetime as _dt
    try:
        from time_utils import parse_ts, now_iso
        return (parse_ts(now_iso()) - parse_ts(_current_gps_updated_at)).total_seconds()
    except Exception:
        return None

def set_current_gps_category(cat: Optional[str], *, observed_at: Optional[str]):
    global _current_gps_category, _current_gps_updated_at
    _current_gps_category = cat if observed_at else None
    _current_gps_updated_at = observed_at if cat else None
_behavior_scheduler_available: bool = False

def get_behavior_scheduler_available() -> bool:
    return _behavior_scheduler_available

def set_behavior_scheduler_available(val: bool):
    global _behavior_scheduler_available
    _behavior_scheduler_available = val
_pending_tasks: dict = {}
_pending_tasks_lock = asyncio.Lock()

def get_pending_tasks() -> dict:
    return dict(_pending_tasks)

def get_pending_tasks_lock() -> asyncio.Lock:
    return _pending_tasks_lock
_wander_manager = None

def get_wander_manager():
    """获取漫想管理器单例（由 main.py startup 设置）"""
    return _wander_manager

def set_wander_manager(mgr):
    global _wander_manager
    _wander_manager = mgr
_context_scheduler = None

def get_context_scheduler():
    return _context_scheduler

def set_context_scheduler(cs):
    global _context_scheduler
    _context_scheduler = cs
_scheduler = None

def get_scheduler():
    return _scheduler

def set_scheduler(s):
    global _scheduler
    _scheduler = s
_last_wb_detection_date: str = ''
_wb_detection_ampm: str = ''

def get_last_wb_detection_date() -> str:
    return _last_wb_detection_date

def set_last_wb_detection_date(val: str):
    global _last_wb_detection_date
    _last_wb_detection_date = val

def get_wb_detection_ampm() -> str:
    return _wb_detection_ampm

def set_wb_detection_ampm(val: str):
    global _wb_detection_ampm
    _wb_detection_ampm = val
_session_op_locks: dict = {}

def get_session_op_lock(session_id: str) -> asyncio.Lock:
    """获取或创建 per-session 操作锁，用于序列化 revoke/delete/edit"""
    if session_id not in _session_op_locks:
        _session_op_locks[session_id] = asyncio.Lock()
    return _session_op_locks[session_id]
_ALLOWED_IPS: set = set()

def resolve_allowed_ips() -> set:
    """从 env 构建 IP 白名单（三处中间件共用，避免硬编码）。

    组合：127.0.0.1（永远允许本地）+ BACKEND_HOST + MOBILE_HOST
    + MOBILE_LAN_HOST + ALLOWED_IPS(逗号分隔)。
    无额外配置时仅允许 IPv4/IPv6 本地回环。
    """
    import os
    ips = {'127.0.0.1', '::1'}
    for key in ('BACKEND_HOST', 'MOBILE_HOST', 'MOBILE_LAN_HOST'):
        v = os.getenv(key, '').strip()
        if v and v not in ('0.0.0.0', 'localhost') and any((c.isdigit() for c in v)):
            ips.add(v)
    extra = os.getenv('ALLOWED_IPS', '').strip()
    if extra:
        for ip in extra.split(','):
            ip = ip.strip()
            if ip:
                ips.add(ip)
    return ips

def get_allowed_ips() -> set:
    return set(_ALLOWED_IPS)

def set_allowed_ips(val: set):
    global _ALLOWED_IPS
    _ALLOWED_IPS = val
import os as _os_img
_current_image_model: str = _os_img.getenv('IMAGE_MODEL', 'flash-vision')

def get_current_image_model() -> str:
    return _current_image_model

def set_current_image_model(model: str) -> None:
    global _current_image_model
    _current_image_model = model
_task_last_success: dict = {}
_task_fail_count: dict = {}
_task_last_error: dict = {}
_cron_last_success: dict = {}
_cron_last_error: dict = {}
_last_reminder_injected_at: str = ''

def get_task_last_success() -> dict:
    return dict(_task_last_success)

def set_task_last_success(task_id: str, timestamp: str):
    _task_last_success[task_id] = timestamp

def get_task_fail_count() -> dict:
    return dict(_task_fail_count)

def incr_task_fail_count(task_id: str):
    _task_fail_count[task_id] = _task_fail_count.get(task_id, 0) + 1

def get_task_last_error() -> dict:
    return dict(_task_last_error)

def set_task_last_error(task_id: str, error: str):
    _task_last_error[task_id] = error

def get_cron_last_success() -> dict:
    return dict(_cron_last_success)

def set_cron_last_success(name: str, timestamp: str):
    _cron_last_success[name] = timestamp

def get_cron_last_error() -> dict:
    return dict(_cron_last_error)

def set_cron_last_error(name: str, error: str):
    _cron_last_error[name] = error

def get_last_reminder_injected_at() -> str:
    return _last_reminder_injected_at

def set_last_reminder_injected_at(val: str):
    global _last_reminder_injected_at
    _last_reminder_injected_at = val
_recently_active_sessions: set = set()

def get_recently_active_sessions() -> set:
    return set(_recently_active_sessions)

def add_recently_active_session(session_id: str):
    _recently_active_sessions.add(session_id)

def discard_recently_active_session(session_id: str):
    _recently_active_sessions.discard(session_id)
_on_topic_ended_callback = None

def get_on_topic_ended_callback():
    return _on_topic_ended_callback

def set_on_topic_ended_callback(cb):
    global _on_topic_ended_callback
    _on_topic_ended_callback = cb

def get_dnd_enabled() -> Optional[bool]:
    """真实布尔勿扰状态；来源失败或坏类型为 None（未知）。"""
    try:
        import settings_manager
        value = settings_manager.get_setting('dnd_enabled')
        return value if isinstance(value, bool) else None
    except Exception:
        return None

def set_dnd_enabled(val: bool):
    """更新 DND 开关。"""
    if not isinstance(val, bool):
        raise TypeError('DND requires a boolean')
    import settings_manager
    settings_manager.set_setting('dnd_enabled', val)

def should_suppress_proactive() -> bool:
    """判断当前是否应压制 AI 的主动表达（哨兵/漫想/状态切换）。

    当前仅检查手动 DND 开关；阶段4 演进为 ProactiveExpressionArbiter（含心流感知调制）。
    """
    return get_dnd_enabled() is not False
import threading as _threading
_wifi_state: dict = {}
_wifi_state_lock = _threading.Lock()

def set_wifi_state(event: dict):
    """由 wander WS handler 调用，推送 WiFi 状态变化。WifiSource.on_event() 同步调用。"""
    global _wifi_state
    with _wifi_state_lock:
        _wifi_state.update(event)
    try:
        from silicon_perception.collection.wifi import get_wifi_source
        src = get_wifi_source()
        if src:
            src.on_event(event)
    except Exception:
        pass

def get_wifi_state() -> dict:
    """获取当前 WiFi 状态快照。"""
    with _wifi_state_lock:
        return dict(_wifi_state)
