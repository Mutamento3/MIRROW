"""Explicit enrollment, isolated from passive listening and chat submission."""
import asyncio
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from .voice_identity import get_voice_identity
from .store import get_ambient_store

router = APIRouter(prefix="/voice-identity")


@router.get("")
def status():
    return get_voice_identity().status()


@router.post("/prepare")
async def prepare(file: UploadFile = File(...), source: str = Form("未注明麦克风")):
    data = await file.read(1280001)
    if len(data) > 1280000:
        raise HTTPException(400, "录音过大，请录 35 秒以内的 WAV")
    try:
        result = await asyncio.to_thread(get_voice_identity().prepare, data, source)
        get_ambient_store().checkpoint("voice-enrollment", "voice_enrollment_prepare", "success")
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        get_ambient_store().checkpoint("voice-enrollment", "voice_enrollment_prepare", "fault",
                                      type(exc).__name__, False, "inspect_local_speaker_model")
        raise HTTPException(503, "本地声纹模型未就绪，请检查监控台") from exc


@router.post("/confirm")
def confirm(data: dict):
    from cognition.other_book import entities
    entity_id = str(data.get("entity_id") or "")
    entity = entities().get(entity_id)
    if not entity or entity.get("type") != "human":
        raise HTTPException(400, "请选择已登记的人类实体")
    if data.get("confirmed_single_speaker") is not True:
        raise HTTPException(400, "请确认本段录音只有所选人物讲话")
    try:
        result = get_voice_identity().confirm(str(data.get("draft_id") or ""), entity_id, entity["name"])
        get_ambient_store().checkpoint("voice-enrollment", "voice_enrollment_confirm", "success")
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{sample_id}")
def delete(sample_id: str):
    if not get_voice_identity().delete(sample_id):
        raise HTTPException(404, "样本不存在")
    return {"deleted": True}
