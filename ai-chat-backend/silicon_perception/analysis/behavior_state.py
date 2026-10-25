"""行为状态推断 — 融合 PC进程+键鼠空闲+手机App，输出自然语言状态。

纯规则引擎，每 tick 运行，零 LLM 调用。输出写入 health_snapshots.behavior_state。

输出示例：
- 人类伙伴在电脑上玩游戏(黎明杀机)
- 人类伙伴在用电脑(Code)
- 人类伙伴在用手机刷小红书
- 人类伙伴同时在用手机(微信)和电脑(Code)
- 人类伙伴电脑在放东西(Chrome)
- 人类伙伴没在设备前
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def infer_behavior_state(
    input_idle_seconds: Optional[float],
    foreground_exe: Optional[str],
    foreground_app: Optional[str],
    screen_active: Optional[bool],
    mobile_app_name: Optional[str],
    mobile_screen_on: Optional[bool] = None,
    phone_session_seconds: Optional[float] = None,
    mobile_vision_description: Optional[str] = None,
    mobile_app_package: Optional[str] = None,
    mobile_vision_package: Optional[str] = None,
    mobile_vision_category: Optional[str] = None,
    mobile_vision_activity: Optional[str] = None,
    mobile_vision_confidence: Optional[float] = None,
    remote_control_active: Optional[bool] = None,
    remote_input_idle_seconds: Optional[float] = None,
    phone_posture: Optional[str] = None,
    phone_motion: Optional[str] = None,
    phone_light: Optional[str] = None,
) -> str:
    """根据多源输入推断当前行为状态，返回自然语言。

    Args:
        phone_session_seconds: 手机连续亮屏秒数。用于区分"瞥一眼"（<2min→保持pc）和"真正双设备使用"（≥2min→both）。
        mobile_vision_description: MobileVisionSource 的轻量视觉分类描述。只有与当前包名、
                                   时效和置信度对齐后才会补充 App 身份。
        remote_control_active: 远程控制软件进程能力标注。进程常驻不代表当前正在远控，
                               不作为行为状态的活动证据。
        remote_input_idle_seconds: 最近一次经 Raw Input 识别的远程键鼠输入距今秒数；
                                   小于行为窗口时表示当前正在远程控制电脑。
        phone_posture/phone_motion/phone_light: 手机自身的姿态、运动和环境光观测。
                               只能描述设备，不能反推人类伙伴的身体姿势或移动原因。

    Returns:
        自然语言行为状态字符串。兼容旧调用方（仍以 phone_/pc_ 前缀 + unknown 枚举）。
    """
    # input_idle_seconds 由 sentinel 层传入已确认的本地物理 idle；远程输入
    # 不会刷新它。Raw Input 还提供独立的远程 idle，未知来源永不刷新本地证据。
    pc_active = input_idle_seconds is not None and input_idle_seconds < 120  # 2min 内操作过
    pc_on = screen_active is True  # 屏幕画面在变化
    # ⚠️ 2026-08-24 v3：手机"正在使用"不依赖 motion——静止刷视频（手机放支架/平放）
    #   也是真实使用，motion 会误判"没在用"。"是否在用"由 mobile_screen_on 判定
    #   （手机端用 isInteractive()：锁屏/AOD=false，亮屏在用=true）。
    phone_active = mobile_screen_on is True and mobile_app_name is not None

    # PC 显示名：优先窗口标题（人类可读），回退 exe 去后缀
    pc_name = _pc_display_name(foreground_exe, foreground_app)

    # App 身份是硬事实；视觉只能在同一包名/会话内做内容补充，不能反向改写身份。
    phone_label = select_mobile_activity_label(
        mobile_app_name=mobile_app_name,
        mobile_app_package=mobile_app_package,
        mobile_vision_description=mobile_vision_description,
        mobile_vision_package=mobile_vision_package,
        mobile_vision_category=mobile_vision_category,
        mobile_vision_activity=mobile_vision_activity,
        mobile_vision_confidence=mobile_vision_confidence,
    )

    # 非本地 Raw Input 是人类伙伴从手机侧操纵 MIRROW 所在电脑的直接证据。
    # 它必须在双设备和普通 PC 分支之前解释；远端造成的窗口/进程变化不能
    # 反过来把人类伙伴判成坐在本机电脑前。
    remote_active = (
        remote_input_idle_seconds is not None
        and remote_input_idle_seconds < 120
    )
    if remote_active:
        if pc_active and pc_name:
            return f"人类伙伴在用手机远程控制电脑({pc_name})，本机也有键鼠活动"
        if pc_active:
            return "人类伙伴在用手机远程控制电脑，本机也有键鼠活动"
        if pc_name:
            return f"人类伙伴在用手机远程控制电脑({pc_name})"
        return "人类伙伴在用手机远程控制电脑"

    # ── 同时用两台设备 ──
    # Case A: PC 活跃（idle<120s）+ 手机连续亮屏 ≥60s → 双设备（瞥一眼通知不算，防 pc→both 抖动触发 L1）
    # 手机亮得久不能反证电脑仍有人操作；双设备必须同时有本地输入证据。
    pc_attended = input_idle_seconds is not None and input_idle_seconds < 120
    phone_60s = (phone_session_seconds or 0) >= 60
    if phone_active and pc_name and pc_attended and phone_60s:
        return f"人类伙伴同时在用手机({phone_label})和电脑({pc_name})"
    if phone_active and pc_attended and phone_60s:
        return f"人类伙伴同时在用手机({phone_label})和电脑"

    # 手机刚亮起不足一分钟只是一次短暂查看；本地物理键鼠仍在 2 分钟
    # 活跃窗口内时，不能反过来把正在电脑前的人类伙伴判成“只用手机”。
    # 等手机连续使用达到上面的双设备门槛后，再切换为 both。
    if phone_active and pc_attended and not phone_60s:
        if pc_name:
            if _is_pc_game(foreground_exe):
                return f"人类伙伴在电脑上玩游戏({pc_name})"
            return f"人类伙伴在用电脑({pc_name})"
        return "人类伙伴在用电脑"

    # ── 只用手机 ──
    if phone_active:
        sensor_notes = []
        if (phone_posture in ("landscape_left", "landscape_right")
                and phone_light in ("dark", "dim") and not pc_active):
            sensor_notes.append("手机横屏，手机光线传感器显示环境较暗")
        if phone_motion in ("moving", "strong"):
            sensor_notes.append(
                "手机运动传感器显示设备移动"
                if phone_motion == "moving" else "手机运动传感器显示设备明显晃动"
            )
        suffix = f"（{'；'.join(sensor_notes)}）" if sensor_notes else ""
        return f"人类伙伴在用手机{phone_label}{suffix}"

    # ── 只用电脑 ──
    if pc_active and pc_name:
        if _is_pc_game(foreground_exe):
            return f"人类伙伴在电脑上玩游戏({pc_name})"
        return f"人类伙伴在用电脑({pc_name})"
    if pc_active:
        return "人类伙伴在用电脑"

    # ── 窗口身份/画面变化是设备事实，不是人类伙伴正在使用的证据 ──
    if not pc_active and (pc_on or input_idle_seconds is None and pc_name):
        window = f"电脑前台窗口为{pc_name}" if pc_name else "电脑画面"
        change = "画面有变化" if pc_on else (
            "本次采样画面未变化" if screen_active is False else "画面变化未知"
        )
        inputs = "本地键鼠输入未知" if input_idle_seconds is None else (
            f"本地键鼠已空闲{int(input_idle_seconds // 60)}分钟"
        )
        return f"{window}；{change}；{inputs}"

    # ── 手机亮屏但无前台 App ──
    if mobile_screen_on and not phone_active:
        return "手机亮屏(无前台App)"

    # ── 设备前没人（双设备空闲 >5min）──
    if not pc_active and not phone_active:
        if input_idle_seconds is not None and input_idle_seconds > 300:
            return "人类伙伴没在设备前"

    # 兜底
    return "unknown"


def behavior_category(state: str) -> str:
    """从 behavior_state 自然语言字符串提取设备类别（前缀匹配）。

    供 baseline 分组、trigger_detector 等共享使用。
    """
    if not state:
        return "unknown"
    if state.startswith("人类伙伴同时在用手机"):
        return "both"
    # 旧版本曾用“躺着/移动中”过度解释手机传感器；保留前缀兼容历史行。
    if state.startswith("人类伙伴躺着在用手机") or state.startswith("人类伙伴在移动中用手机"):
        return "phone"
    if state.startswith("人类伙伴在用手机"):
        return "phone"
    if state.startswith("人类伙伴在用电脑") or state.startswith("人类伙伴在电脑上玩游戏"):
        return "pc"
    if state.startswith("人类伙伴电脑在放东西"):
        return "pc"
    if state.startswith("手机亮屏"):
        return "phone"
    if state.startswith("人类伙伴没在设备前"):
        return "away"
    return "unknown"


def select_mobile_activity_label(
    *,
    mobile_app_name: Optional[str],
    mobile_app_package: Optional[str],
    mobile_vision_description: Optional[str],
    mobile_vision_package: Optional[str],
    mobile_vision_category: Optional[str],
    mobile_vision_activity: Optional[str],
    mobile_vision_confidence: Optional[float],
) -> str:
    """Return a grounded phone label without letting vision replace app identity.

    A screenshot is supporting evidence for the exact foreground package captured with it.
    In particular, a landscape stream inside a video or remote-control app must not become
    a mobile-game fact merely because its pixels resemble a game.
    """
    base = mobile_app_name or "手机"
    if not mobile_vision_description:
        return base
    if not mobile_app_package or mobile_vision_package != mobile_app_package:
        return base
    try:
        if float(mobile_vision_confidence or 0.0) < 0.75:
            return base
    except (TypeError, ValueError):
        return base

    evidence = " ".join(filter(None, (
        mobile_vision_category,
        mobile_vision_activity,
        mobile_vision_description,
    ))).lower()
    game_like = any(token in evidence for token in ("game", "gaming", "游戏", "打游戏", "玩游戏"))
    if game_like:
        from silicon_perception.collection.app_name_map import is_confirmed_game_package
        if not is_confirmed_game_package(mobile_app_package):
            return base
    description = str(mobile_vision_description).strip()
    if not description or description == base:
        return base
    if len(description) > 60:
        description = description[:60] + "…"
    # 始终保留包名解析出的 App 身份；视觉只作为同一会话的括号内容。
    return f"{base}（{description}）"


def _pc_display_name(exe: Optional[str], app: Optional[str]) -> Optional[str]:
    """PC 进程名 → 人类可读显示名。
    优先用 exe 精简名（精确稳定），回退到窗口标题。
    """
    if exe:
        name = exe.rsplit(".exe", 1)[0]
        # 精简常见后缀
        for suffix in ("-Win64-Shipping", "-win64-shipping", "-Win64", "-win64", ".exe"):
            if name.endswith(suffix):
                name = name[:-len(suffix)]
                break
        return name
    if app and len(app) > 1:
        # 截断过长标题
        return app[:50] + "..." if len(app) > 50 else app
    return None


def _is_pc_game(exe: Optional[str]) -> bool:
    """Only use game wording when the shared process catalog has hard evidence."""
    try:
        from silicon_perception.state.process_catalog import get_catalog
        return get_catalog().is_game(exe)
    except Exception:
        return False
