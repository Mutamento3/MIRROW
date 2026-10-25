from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .policy import (VALID_MODES, capture_decision, contact_decision,
                     effective_listening_thresholds, listening_thresholds)
from . import gpu_pilot
from .pc_capture import microphones, selected_microphone
from .source_routing import configured_microphone
from .service import get_ambient_listening_service
from .store import get_ambient_store, utc_now
from .voice_identity import get_voice_identity
from .enrollment_router import router as enrollment_router


router = APIRouter(prefix="/api/ambient-listening", tags=["ambient-listening"])
router.include_router(enrollment_router)


@router.get("/status")
def status():
    return {
        "capture": capture_decision().as_dict(),
        "contact": contact_decision().as_dict(),
        "thresholds": listening_thresholds(),
        "effective_thresholds": effective_listening_thresholds(),
        "runtime": get_ambient_store().status(),
        "source": get_ambient_listening_service().source_status(),
        "pc_microphone_selection": configured_microphone(),
        "gpu_pilot": {"enabled": gpu_pilot.enabled(), "ready": gpu_pilot.ready(),
                      "model": gpu_pilot.MODEL_LABEL,
                      "min_start_interval_seconds": gpu_pilot.MIN_START_INTERVAL_SECONDS},
        "voice_identity": get_voice_identity().status(),
    }


@router.post("/mode")
async def set_mode(data: dict):
    mode = str(data.get("mode") or "")
    if mode not in VALID_MODES:
        raise HTTPException(400, "mode 必须是 off、smart 或 on")
    from settings_manager import set_setting
    set_setting("ambient_listening_mode", mode)
    await get_ambient_listening_service()._sync_capture_source()
    return {"mode": mode, "capture": capture_decision().as_dict()}


@router.get("/microphones")
def pc_microphones():
    return {"items": [{"name": item.name, "sample_rate": item.sample_rate}
                      for item in microphones()],
            "selected": configured_microphone()}


@router.post("/microphone")
async def set_pc_microphone(data: dict):
    name = str(data.get("name") or "").strip()
    if len(name) > 160:
        raise HTTPException(400, "麦克风名称过长")
    device = selected_microphone(name) if name else None
    if name and device is None:
        raise HTTPException(400, "电脑输入设备当前不可用")
    from settings_manager import set_setting
    selected = device.name if device else ""
    set_setting("ambient_pc_microphone", selected)
    await get_ambient_listening_service()._sync_capture_source()
    return {"selected": selected}


@router.post("/thresholds")
def set_thresholds(data: dict):
    unknown = set(data) - set(listening_thresholds())
    if unknown:
        raise HTTPException(400, f"未知阈值: {', '.join(sorted(unknown))}")
    parsed = listening_thresholds(data)
    try:
        for key, value in data.items():
            if int(value) != parsed[key]:
                raise ValueError(key)
    except (TypeError, ValueError, KeyError):
        raise HTTPException(400, "阈值超出允许范围或格式不正确")
    if parsed["timed_min_chars"] > parsed["immediate_chars"]:
        raise HTTPException(400, "时间兜底字符数不能大于充足材料字符数")
    from settings_manager import set_setting
    set_setting("ambient_listening_thresholds", parsed)
    return {"thresholds": parsed, "capture": capture_decision().as_dict()}


@router.get("/checkpoints")
def checkpoints(limit: int = 80):
    return {"items": get_ambient_store().checkpoints(limit), "contains_body_text": False}


@router.get("/records")
def records(limit: int = 80):
    """Local, read-only projection for the monitor; all rows expire with 24h material."""
    store = get_ambient_store()
    payload = store.monitor_records(limit)
    return {
        "generated_at": utc_now(),
        "retention_hours": 24,
        "materials": payload["materials"],
        "decisions": payload["decisions"],
        "runtime": store.status(),
        "source": get_ambient_listening_service().source_status(),
        "checkpoints": store.checkpoints(min(max(int(limit), 1), 80)),
        "contains_body_text": True,
        "contains_file_paths": False,
        "contains_hidden_reasoning": False,
    }


@router.get("/bindings")
def bindings():
    identity = get_voice_identity().status()
    return {"items": identity["samples"], "voice_identity_available": identity["available"]}


@router.post("/bindings")
def bind(data: dict):
    # Legacy clients cannot invent a speaker key without a verified enrollment.
    raise HTTPException(409, "请通过声纹录入与本人确认入口绑定")


@router.get("/mobile-config")
def mobile_config():
    return get_ambient_listening_service().mobile_config()
