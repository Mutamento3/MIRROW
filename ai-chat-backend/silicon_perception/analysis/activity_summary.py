"""用户活动统一摘要 — 纯规则引擎，零 LLM 调用。

整合设备行为、位置、状态声明、聊天活跃度、音乐等所有可感知维度，
输出第一人称自然语言描述，供 AI 的上下文注入。

与 sentinel_snapshot 分工：
- activity_summary → 行为与情境：她在做什么、在哪儿、能联系到吗
- sentinel_snapshot → 健康传感器：心率、步数、生理期

抑制逻辑：
- 同维度内高置信度抑制低置信度（WiFi 抑制 GPS）
- 不同维度可以矛盾（声明≠观察），矛盾留给 AI 判断
"""

from typing import Optional


# ═══════════════════════════════════════════════════════════
# 模块级默认阈值（可被函数参数覆盖）
# ═══════════════════════════════════════════════════════════

_DEFAULT_IDLE_SHOW_SEC = 300         # PC 闲置此秒数以上不显示应用会话
_DEFAULT_GPS_MAX_AGE_SEC = 1800      # GPS 过期此秒数以上走"位置未知"（与 arbiter._GPS_MAX_AGE 对齐）
_DEFAULT_WIFI_MAX_AGE_SEC = 300
_DEFAULT_CHAT_SILENCE_HOURS = 24     # 私聊+群聊最新超过此时数 → 合并为"X天没有登上MIRROW"


# ═══════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════

def build_activity_summary(
    # 设备
    behavior_state: Optional[str] = None,
    input_idle_seconds: Optional[float] = None,
    # 位置
    location_category: Optional[str] = None,
    location_address: Optional[str] = None,
    wifi_is_home: Optional[bool] = None,
    # 状态声明
    user_status: Optional[str] = None,
    status_duration_sec: Optional[float] = None,
    # 应用会话
    active_app_session: Optional[str] = None,
    # 私聊
    last_private_chat_sec: Optional[float] = None,
    # 群聊
    group_chat_today_has_msg: bool = False,
    last_group_chat_sec: Optional[float] = None,
    # 音乐
    music_song_title: Optional[str] = None,
    music_song_artist: Optional[str] = None,
    # 时效（field_name → age_seconds，用于位置过期判断）
    field_ages: Optional[dict] = None,
    # 可配阈值
    idle_show_sec: int = _DEFAULT_IDLE_SHOW_SEC,
    gps_max_age_sec: int = _DEFAULT_GPS_MAX_AGE_SEC,
    chat_silence_hours: int = _DEFAULT_CHAT_SILENCE_HOURS,
    location_place_name: Optional[str] = None,
    location_environment: Optional[str] = None,
    user_status_detail: Optional[str] = None,
    user_status_source: Optional[str] = None,
    wifi_connected: Optional[bool] = None,
) -> str:
    """构建统一的用户当前状态自然语言摘要。

    纯规则引擎，零 LLM 调用。每行一个维度，仅输出有数据的维度。
    返回空字符串表示所有维度均无可用数据。
    """
    lines = []
    field_ages = field_ages or {}

    # ── 决定位置行的输出 ──
    gps_age = field_ages.get("gps")  # seconds, None if unknown
    gps_fresh = gps_age is not None and gps_age < gps_max_age_sec
    gps_category = location_category

    # ── 决定消息沉默 ──
    # 取私聊和群聊中较新的作为"最后活跃"
    latest_msg_sec = _min_optional(last_private_chat_sec, last_group_chat_sec)
    is_silent = latest_msg_sec is not None and latest_msg_sec > chat_silence_hours * 3600

    # ── 1. 设备行为 ──
    if behavior_state:
        lines.append(behavior_state)

    # ── 2. 位置 ──
    # WiFi 连家 → 最高置信度，抑制 GPS
    wifi_age = field_ages.get("wifi")
    wifi_fresh = wifi_age is not None and wifi_age < _DEFAULT_WIFI_MAX_AGE_SEC
    if wifi_is_home and wifi_fresh:
        lines.append("在家")
    elif location_place_name and field_ages.get("gps_place") is not None and field_ages["gps_place"] < gps_max_age_sec:
        from silicon_perception.location_places import place_observation
        lines.append(place_observation(location_place_name, location_environment,
                                       wifi_connected if wifi_fresh else None))
    elif gps_category and gps_fresh:
        addr_short = _shorten_address(location_address)
        if gps_category == "home":
            lines.append(f"在家({addr_short})" if addr_short else "在家")
        elif gps_category == "work":
            lines.append(f"在公司地点范围内({addr_short})" if addr_short else "在公司地点范围内")
        else:  # elsewhere
            lines.append(f"外出({addr_short})，室内外未知" if addr_short else "外出，室内外未知")
    elif gps_category and not gps_fresh:
        # GPS 过期 + 无 WiFi 锚点 → 位置未知
        if gps_age is not None:
            age_str = _fmt_duration_long(gps_age)
            lines.append(f"位置未知(已过期{age_str})")
        else:
            lines.append("位置未知")
    elif wifi_is_home is False and wifi_fresh:
        lines.append("外出，室内外未知")

    # ── 3. 状态声明（始终显示）──
    status_line = _build_status_line(user_status, status_duration_sec)
    if status_line:
        if user_status_source == "auto":
            status_line = status_line.replace("她声明", "系统推断她")
        elif user_status_source in {"unknown", "latent"}:
            status_line = status_line.replace("她声明", "系统记录她") + "（声明来源未确认）"
        if user_status_detail and user_status_source == "explicit":
            status_line += f"；手动填写的状态描述：{user_status_detail}"
        lines.append(status_line)

    # ── 4. 应用会话 ──
    # 仅在设备前有人时显示（"没在设备前"抑制应用会话）
    away = behavior_state and "没在设备前" in behavior_state
    if active_app_session and not away:
        idle = input_idle_seconds
        if idle is not None and idle < idle_show_sec:
            lines.append(active_app_session)

    # ── 5. 聊天活跃度 ──
    if is_silent:
        # >24h → 合并为一句
        days = int(latest_msg_sec / 86400) if latest_msg_sec else 1
        if days < 1:
            days = 1
        lines.append(f"已经{days}天没有登上MIRROW了")
    else:
        # 私聊
        if last_private_chat_sec is not None:
            lines.append(_fmt_private_chat(last_private_chat_sec))
        # 群聊
        if group_chat_today_has_msg and last_group_chat_sec is not None:
            lines.append(_fmt_group_chat(last_group_chat_sec))
        elif not group_chat_today_has_msg:
            lines.append("今天没在群聊里冒过泡")

    # ── 6. 音乐 ──
    if music_song_title:
        artist_str = f" - {music_song_artist}" if music_song_artist else ""
        lines.append(f"正在听《{music_song_title}》{artist_str}")

    return "。".join(lines) if lines else ""


# ═══════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════

def _min_optional(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """返回两个“距现在秒数”中较小的，即最近一次活动。"""
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _build_status_line(status: Optional[str], duration_sec: Optional[float]) -> str:
    """构建状态声明行（含 location 层级信息）。"""
    if not status:
        return ""
    status_lower = status.lower()
    if status_lower == "idle":
        # 动态 location：在家·空闲 vs 外出 vs 未知
        try:
            from wander_manager.user_status import get_status_meta
            meta = get_status_meta()
            if meta and meta.location == "out":
                return "她当前不在主家，没特意说在做什么"
            elif meta and meta.location == "home":
                return "她在家，没特意说在做什么"
        except Exception:
            pass
        return "她在家，没特意说在做什么"
    if status_lower == "out":
        duration = f"（此状态已持续{_fmt_duration_long(duration_sec)}）" if duration_sec and duration_sec >= 60 else ""
        return f"她声明当前不在主家{duration}"
    # 状态名中文映射（legacy string 不变）
    status_cn = _STATUS_CN_MAP.get(status_lower, status)
    # eating 加 location 区分
    if status_lower == "eating":
        try:
            from wander_manager.user_status import get_status_meta
            meta = get_status_meta()
            if meta and meta.location == "out":
                status_cn = "在外面吃饭"
            elif meta and meta.location == "home":
                status_cn = "在家吃饭"
        except Exception:
            pass
    if duration_sec and duration_sec >= 60:
        dur_str = _fmt_duration_long(duration_sec)
        return f"她声明已经{status_cn}了{dur_str}"
    return f"她声明正在{status_cn}"


_STATUS_CN_MAP = {
    "out": "外出",
    "sleeping": "睡觉",
    "napping": "小睡",
    "gaming": "玩游戏",
    "coding": "码代码",
    "eating": "吃饭",
    "bathing": "洗澡",
    "busy": "在忙",
}


def _shorten_address(address: Optional[str]) -> str:
    """从高德地址中提取最具体的一段（区/街道/建筑）。"""
    if not address:
        return ""
    # 高德地址格式：省-市-区-街道-……
    # 取去掉省市区后的剩余部分，最长 10 字
    parts = [p.strip() for p in address.replace("省", " ").replace("市", " ").replace("区", " ").split() if p.strip()]
    if not parts:
        return address[:10]
    # 去掉前两级（省市），保留从区级开始的
    specific = "".join(parts[2:]) if len(parts) > 2 else "".join(parts)
    # 去重复字符
    return specific[:10] if specific else address[:10]


def _fmt_duration_long(sec: float) -> str:
    """格式化秒数为中文长描述（含天）。"""
    if sec < 60:
        return "刚刚"
    m = int(sec / 60)
    if m < 60:
        return f"{m}分钟"
    h = m // 60
    if h < 24:
        rm = m % 60
        if rm == 0:
            return f"{h}小时"
        return f"{h}小时{rm}分钟"
    days = h // 24
    rh = h % 24
    if rh == 0:
        return f"{days}天"
    return f"{days}天{rh}小时"


def _fmt_private_chat(sec: float) -> str:
    """私聊时间行。≤1h → '上次私聊是X分钟前'，>1h → '已经X小时没有说话了'"""
    if sec < 60:
        return "上次私聊是刚刚"
    m = int(sec / 60)
    if m < 60:
        return f"上次私聊是{m}分钟前"
    h = m // 60
    if h == 1:
        return "已经1小时没有说话了"
    if h < 24:
        return f"已经{h}小时没有说话了"
    days = h // 24
    return f"已经{days}天没有说话了"


def _fmt_group_chat(sec: float) -> str:
    """群聊时间行。始终用'上次水群'格式（>24h 已被上层抑制）。"""
    if sec < 60:
        return "上次水群是刚刚"
    m = int(sec / 60)
    if m < 60:
        return f"上次水群是{m}分钟前"
    h = m // 60
    if h == 1:
        return "上次水群是1小时前"
    rm = m % 60
    if rm == 0:
        return f"上次水群是{h}小时前"
    return f"上次水群是{h}小时{rm}分钟前"
