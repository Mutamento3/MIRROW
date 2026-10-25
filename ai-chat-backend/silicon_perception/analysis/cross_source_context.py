"""Cross-source semantic observations built from the existing snapshot.

This module stops at language-ready facts.  It does not mutate user status,
diagnose intent, decide whether AI should speak, or execute tools.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


_REST_STATUSES = {"sleeping", "napping"}
_SLEEP_STAGE_CN = {
    "sleep": "睡眠分钟",
    "light": "浅睡",
    "deep": "深睡",
    "rem": "REM睡眠",
    "awake": "清醒",
}
_PASSIVE_PHONE_PACKAGES = {
    "com.android.systemui",
    "com.oplus.launcher",
    "com.android.launcher",
    "com.android.launcher3",
}


@dataclass(frozen=True)
class SemanticObservation:
    key: str
    state: str
    text: str
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    observed_at: str = ""
    confidence: str = "medium"
    attention_eligible: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_local(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return None


def _duration_cn(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    if minutes < 60:
        return f"{minutes}分钟"
    hours, remainder = divmod(minutes, 60)
    return f"{hours}小时{remainder}分钟" if remainder else f"{hours}小时"


def _activity_evidence(snapshot) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []

    if not snapshot.is_stale("current_app", 300) and snapshot.mobile_screen_on is True:
        package = (snapshot.mobile_app_package or "").strip()
        if package and package not in _PASSIVE_PHONE_PACKAGES:
            app_name = (snapshot.mobile_app_name or package).strip()
            evidence.append({
                "source": "current_app",
                "kind": "phone_foreground",
                "age_seconds": snapshot.age_of("current_app"),
                "app_name": app_name,
                "package": package,
                "text": f"手机亮屏，前台内容应用为{app_name}",
            })

    if not snapshot.is_stale("input_idle", 180):
        local_idle = snapshot.input_idle_seconds
        if local_idle is not None and local_idle < 120:
            evidence.append({
                "source": "input_idle",
                "kind": "local_raw_input",
                "age_seconds": float(local_idle),
                "text": f"最近{max(0, int(local_idle))}秒内检测到本地物理键鼠输入",
            })
        remote_idle = getattr(snapshot, "remote_input_idle_seconds", None)
        if remote_idle is not None and remote_idle < 120:
            evidence.append({
                "source": "input_idle",
                "kind": "remote_raw_input",
                "age_seconds": float(remote_idle),
                "text": f"最近{max(0, int(remote_idle))}秒内检测到远程键鼠输入",
            })
    return evidence


def _read_sleep_stage(reader: Optional[Callable[..., Optional[dict]]], now: datetime) -> Optional[dict]:
    if reader is None:
        try:
            from miband.store import latest_sleep_stage
            reader = latest_sleep_stage
        except Exception:
            return None
    try:
        return reader(now=now.timestamp())
    except TypeError:
        return reader()
    except Exception:
        return None


def _build_rest_observations(
    snapshot,
    *,
    now: Optional[datetime] = None,
    rest_review_after_seconds: int = 30 * 60,
    sleep_reader: Optional[Callable[..., Optional[dict]]] = None,
) -> List[dict]:
    """Build the declaration/device comparison without deciding what it means."""
    status = str(getattr(snapshot, "user_status", "") or "").strip().lower()
    if status not in _REST_STATUSES:
        return []

    now = now or datetime.now()
    since = _parse_local(getattr(snapshot, "user_status_since", None))
    declaration_age = None if since is None else max(0.0, (now - since).total_seconds())
    duration_text = "时刻未知" if declaration_age is None else f"{_duration_cn(declaration_age)}前"
    status_cn = "睡眠" if status == "sleeping" else "小憩"
    activity = _activity_evidence(snapshot)
    sleep = _read_sleep_stage(sleep_reader, now)
    fresh_stage = bool(sleep and sleep.get("fresh"))
    fresh_sleep = bool(fresh_stage and sleep.get("stage") in {"sleep", "light", "deep", "rem"})
    fresh_awake = bool(fresh_stage and sleep.get("stage") == "awake")
    observed_at = now.isoformat()

    evidence: List[Dict[str, Any]] = [{
        "source": "user_status",
        "kind": "declaration",
        "status": status,
        "declared_at": getattr(snapshot, "user_status_since", None),
        "age_seconds": declaration_age,
    }]
    evidence.extend(activity)
    if sleep:
        evidence.append({
            "source": sleep.get("source", "mi_band_7_activity_sleep_kind"),
            "kind": "sleep_minute_observation",
            "stage": sleep.get("stage"),
            "measured_at": sleep.get("measured_at"),
            "age_seconds": sleep.get("age_seconds"),
            "fresh": fresh_stage,
        })

    activity_text = "；".join(item["text"] for item in activity)
    if fresh_sleep:
        stage = _SLEEP_STAGE_CN.get(str(sleep.get("stage")), str(sleep.get("stage")))
        sleep_age = _duration_cn(float(sleep.get("age_seconds") or 0))
        if activity:
            text = (
                f"人类伙伴在{duration_text}声明{status_cn}；手环最近一次原生记录为{stage}"
                f"（设备测量距今{sleep_age}），同时本次观测到：{activity_text}。"
                "原生分期提示未经厂商算法校准；这些观测的时间窗口可能重叠，"
                "当前只标记为混合证据，不据此确认是否仍在睡眠。"
            )
            state = "rest_mixed_device_evidence"
        else:
            text = (
                f"人类伙伴在{duration_text}声明{status_cn}；手环最近一次原生记录为{stage}"
                f"（设备测量距今{sleep_age}）。这是设备历史同步观测，不是持续实时判定；"
                "原生分期提示未经厂商算法校准。"
            )
            state = "rest_device_sleep_observed"
        return [SemanticObservation(
            key="rest_declaration_alignment",
            state=state,
            text=text,
            evidence=evidence,
            observed_at=observed_at,
            confidence="medium",
            attention_eligible=False,
        ).to_dict()]

    if fresh_awake and declaration_age is not None and declaration_age >= rest_review_after_seconds:
        awake_age = _duration_cn(float(sleep.get("age_seconds") or 0))
        suffix = f"；同时本次观测到：{activity_text}" if activity else ""
        text = (
            f"人类伙伴在{duration_text}声明{status_cn}；手环最近一次分钟级阶段估计为清醒"
            f"（设备测量距今{awake_age}）{suffix}。这是设备同步的阶段估计，"
            "只说明近期存在清醒证据，不说明原因，也不能单独确认当前状态。"
        )
        state = "rest_declaration_with_device_awake_evidence"
        eligible = True
    elif declaration_age is None or declaration_age < rest_review_after_seconds:
        text = (
            f"人类伙伴在{duration_text}声明{status_cn}。仍处于声明后的观察宽限期；"
            "当前设备信号不用于判断是否已经入睡。"
        )
        state = "rest_declaration_grace_period"
        eligible = False
    elif activity:
        text = (
            f"人类伙伴在{duration_text}声明{status_cn}；本次观测到：{activity_text}。"
            "当前没有足够新的手环睡眠阶段记录；这些事实只说明声明后的设备活动仍可见，"
            "不说明原因，也不能单凭它们确认是否已入睡。"
        )
        state = "rest_declaration_with_recent_device_activity"
        eligible = True
    else:
        text = (
            f"人类伙伴在{duration_text}声明{status_cn}；当前没有足够新的手环睡眠阶段记录，"
            "也没有新鲜的手机前台或键鼠活动证据。数据不足，不能确认当前睡眠阶段。"
        )
        state = "rest_declaration_sleep_state_unavailable"
        eligible = False

    return [SemanticObservation(
        key="rest_declaration_alignment",
        state=state,
        text=text,
        evidence=evidence,
        observed_at=observed_at,
        confidence="medium" if activity else "low",
        attention_eligible=eligible,
    ).to_dict()]


def _mobile_vision_alignment(snapshot, now: datetime) -> Optional[dict]:
    """Expose session mismatch so a screenshot can never relabel another app."""
    if snapshot.is_stale("current_app", 300) or snapshot.is_stale("mobile_vision", 300):
        return None
    current_package = str(getattr(snapshot, "mobile_app_package", "") or "").strip()
    vision_package = str(getattr(snapshot, "mobile_vision_package", "") or "").strip()
    if not current_package or not vision_package or current_package == vision_package:
        return None
    current_name = str(getattr(snapshot, "mobile_app_name", "") or current_package).strip()
    return SemanticObservation(
        key="mobile_visual_session_alignment",
        state="mobile_visual_session_mismatch",
        text=(
            f"手机当前前台应用为{current_name}（{current_package}），但最近一次视觉截图属于"
            f"{vision_package}。两次观测不属于同一应用会话，因此视觉描述不作为当前应用内容。"
        ),
        evidence=[
            {
                "source": "current_app", "kind": "foreground_identity",
                "package": current_package, "age_seconds": snapshot.age_of("current_app"),
            },
            {
                "source": "mobile_vision", "kind": "screenshot_session",
                "package": vision_package, "age_seconds": snapshot.age_of("mobile_vision"),
            },
        ],
        observed_at=now.isoformat(),
        confidence="high",
        attention_eligible=False,
    ).to_dict()


def _location_alignment(snapshot, now: datetime) -> Optional[dict]:
    """Describe a short-lived Wi-Fi/GPS disagreement without creating a new authority."""
    if snapshot.is_stale("wifi", 300) or snapshot.is_stale("gps", 1800):
        return None
    wifi_home = getattr(snapshot, "wifi_is_home", None)
    gps_category = str(getattr(snapshot, "location_category", "") or "").strip().lower()
    if wifi_home is None or not gps_category:
        return None
    gps_home = gps_category == "home"
    if bool(wifi_home) == gps_home:
        return None
    wifi_text = "家庭WiFi已连接" if wifi_home else "当前未连接家庭WiFi"
    gps_text = "home" if gps_home else gps_category
    return SemanticObservation(
        key="location_source_alignment",
        state="location_sources_temporarily_disagree",
        text=(
            f"位置来源在本次窗口内并不一致：{wifi_text}，GPS分类为{gps_text}。"
            "当前粗粒度在家/外出状态仍沿用WiFi权威；GPS可能来自不同采集时刻，"
            "只保留为来源差异，不据此推断移动原因。"
        ),
        evidence=[
            {
                "source": "wifi", "kind": "home_network_match", "value": wifi_home,
                "age_seconds": snapshot.age_of("wifi"),
            },
            {
                "source": "gps", "kind": "structured_location_category",
                "value": gps_category, "age_seconds": snapshot.age_of("gps"),
            },
        ],
        observed_at=now.isoformat(),
        confidence="high",
        attention_eligible=False,
    ).to_dict()


def _heart_rate_activity_context(snapshot, previous_snapshot, now: datetime) -> Optional[dict]:
    """Place an elevated reading beside concurrent movement facts, not a diagnosis."""
    heart_rate = getattr(snapshot, "heart_rate", None)
    if heart_rate is None or heart_rate < 100 or snapshot.is_stale("heart_rate", 900):
        return None

    evidence: List[Dict[str, Any]] = [{
        "source": "heart_rate", "kind": "single_reading", "value": heart_rate,
        "age_seconds": snapshot.age_of("heart_rate"),
    }]
    activity_text: List[str] = []

    motion = str(getattr(snapshot, "phone_motion", "") or "").strip().lower()
    if not snapshot.is_stale("sensor", 600) and motion in {"moving", "strong"}:
        motion_cn = "移动中" if motion == "moving" else "明显晃动"
        activity_text.append(f"手机自身运动传感器为{motion_cn}")
        evidence.append({
            "source": "sensor_context", "kind": "phone_motion", "value": motion,
            "age_seconds": snapshot.age_of("sensor"),
        })

    current_steps = getattr(snapshot, "steps_today", None)
    previous_steps = getattr(previous_snapshot, "steps_today", None) if previous_snapshot else None
    if (
        current_steps is not None and previous_steps is not None
        and current_steps > previous_steps
        and not snapshot.is_stale("steps", 900)
    ):
        delta = int(current_steps - previous_steps)
        activity_text.append(f"相邻有效步数观测增加{delta}步")
        evidence.append({
            "source": "step_count", "kind": "positive_delta", "value": delta,
            "age_seconds": snapshot.age_of("steps"),
        })

    if not activity_text:
        return None
    return SemanticObservation(
        key="heart_rate_activity_context",
        state="heart_rate_with_concurrent_activity_evidence",
        text=(
            f"本次手环心率读数为{heart_rate}次/分；同一观测窗口还看到"
            f"{'；'.join(activity_text)}。这里只并列生理读数与活动证据，"
            "不能据此确定心率变化原因或健康状态。"
        ),
        evidence=evidence,
        observed_at=now.isoformat(),
        confidence="medium",
        attention_eligible=False,
    ).to_dict()


def build_cross_source_observations(
    snapshot,
    *,
    now: Optional[datetime] = None,
    rest_review_after_seconds: int = 30 * 60,
    sleep_reader: Optional[Callable[..., Optional[dict]]] = None,
    previous_snapshot=None,
) -> List[dict]:
    """Build a small set of language-ready facts on the existing snapshot.

    Observations are deliberately sparse: only a declaration comparison, a
    source disagreement, or physiology with concurrent activity becomes a
    semantic item.  Ordinary single-source values stay in their own builders.
    """
    now = now or datetime.now()
    observations = _build_rest_observations(
        snapshot,
        now=now,
        rest_review_after_seconds=rest_review_after_seconds,
        sleep_reader=sleep_reader,
    )
    for builder in (
        _mobile_vision_alignment,
        _location_alignment,
    ):
        item = builder(snapshot, now)
        if item:
            observations.append(item)
    heart_context = _heart_rate_activity_context(snapshot, previous_snapshot, now)
    if heart_context:
        observations.append(heart_context)
    return observations
