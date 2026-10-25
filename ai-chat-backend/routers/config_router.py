"""配置路由 — 设置、图片模型、截断配置"""
import asyncio
import logging
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
logger = logging.getLogger(__name__)
router = APIRouter(tags=['config'])
_main_model_switch_lock = asyncio.Lock()

class ImageModelConfigRequest(BaseModel):
    image_model: str = 'flash-vision'

class MainChatModelRequest(BaseModel):
    profile_id: str

@router.get('/api/settings')
async def api_get_settings():
    from settings_manager import get_settings
    return get_settings()

@router.post('/api/settings')
async def api_update_setting(request: dict):
    from settings_manager import set_setting
    from shared_state import set_latest_persona_prompt
    key = request.get('key')
    value = request.get('value')
    if not key:
        raise HTTPException(status_code=400, detail='缺少 key 字段')
    if key == 'dnd_enabled':
        if not isinstance(value, bool):
            raise HTTPException(status_code=400, detail='勿扰开关需要布尔值')
        from shared_state import set_dnd_enabled
        set_dnd_enabled(value)
        return {'status': 'ok', 'key': key, 'dnd_enabled': value}
    if key == 'silicon_perception' and isinstance(value, dict):
        gps = value.get('gps')
        if isinstance(gps, dict) and 'places' in gps:
            from silicon_perception.location_places import validate_places
            try:
                value = {**value, 'gps': {**gps, 'places': validate_places(gps['places'])}}
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail='落脚处需填写名称、有效经纬度及20–2000米识别半径')
        elif isinstance(gps, dict):
            from settings_manager import get_setting
            existing = ((get_setting(key) or {}).get('gps') or {}).get('places')
            if existing is not None:
                value = {**value, 'gps': {**gps, 'places': existing}}
    set_setting(key, value)
    if key == 'aiPersona' and isinstance(value, dict):
        prompt = value.get('systemPrompt', '')
        if prompt:
            set_latest_persona_prompt(prompt)
    if key == 'user_schedule' and isinstance(value, dict):
        try:
            from silicon_perception.recording.health_store import get_store
            get_store().upsert_user_schedule(work_start=value.get('work_start'), work_end=value.get('work_end'), lunch_start=value.get('lunch_start'), lunch_end=value.get('lunch_end'))
        except Exception:
            pass
    if key == 'silicon_perception' and isinstance(value, dict):
        try:
            gps = value.get('gps', {})
            if gps:
                from silicon_perception.recording.health_store import get_store
                get_store().upsert_user_schedule(home_lat=gps.get('home_lat'), home_lng=gps.get('home_lng'), work_lat=gps.get('work_lat'), work_lng=gps.get('work_lng'), home_radius_m=gps.get('home_radius_m'), work_radius_m=gps.get('work_radius_m'), amap_api_key=gps.get('amap_api_key'))
        except Exception:
            pass
        try:
            hrd = value.get('hr_device') or {}
            if hrd:
                from silicon_perception.recording.health_store import get_store
                get_store().set_hr_device(mac=hrd.get('mac'), name=hrd.get('name'))
        except Exception:
            pass
    return {'status': 'ok', 'key': key}

@router.get('/api/truncation-configs')
async def api_get_truncation_configs():
    from truncation_config import get_all_truncation_configs
    return get_all_truncation_configs()

@router.post('/api/config/image-model')
async def set_image_model(request: ImageModelConfigRequest):
    from shared_state import set_current_image_model
    from behavior_scheduler import get_global_scheduler
    image_model = request.image_model
    if image_model not in ('deepseek', 'glm', 'flash-vision'):
        raise HTTPException(status_code=400, detail="image_model 必须是 'deepseek' 或 'glm' 或 'flash-vision'")
    set_current_image_model(image_model)
    scheduler = await get_global_scheduler()
    if scheduler:
        scheduler.set_image_model(image_model)
    return {'success': True, 'image_model': image_model}

@router.get('/api/config/image-model')
async def get_image_model():
    from shared_state import get_current_image_model
    return {'image_model': get_current_image_model()}

@router.get('/api/config/main-chat-model')
async def get_main_chat_model():
    """Return the safe profile catalog and the adapter currently serving chat."""
    from behavior_scheduler import get_global_scheduler
    from main_model_profiles import public_main_model_state
    active_profile_id = None
    try:
        scheduler = await get_global_scheduler()
        active_profile_id = scheduler.main_model_profile_id
    except RuntimeError:
        pass
    return public_main_model_state(active_profile_id)

@router.post('/api/config/main-chat-model')
async def set_main_chat_model(request: MainChatModelRequest):
    """Switch subsequent main-chat calls and persist the selected id in .env."""
    from behavior_scheduler import get_global_scheduler
    from main_model_profiles import get_main_model_profile, persist_main_model_profile, public_main_model_state
    try:
        profile = get_main_model_profile(request.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not profile.available:
        missing = '、'.join(profile.missing_env) or 'provider 配置'
        raise HTTPException(status_code=409, detail=f'该模型尚不可用，请先在 .env 配置：{missing}')
    try:
        scheduler = await get_global_scheduler()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail='行为调度器尚未就绪') from exc
    async with _main_model_switch_lock:
        if scheduler.main_model_profile_id == profile.id:
            return {'success': True, **public_main_model_state(profile.id)}
        replacement = None
        try:
            replacement = scheduler.build_main_model_adapter(provider=profile.provider, api_key=profile.api_key, base_url=profile.base_url, model=profile.model)
            from api_sources.model_switch import activate_with_event
            previous = activate_with_event(scheduler, replacement, profile, persist_main_model_profile)
            replacement = None
            if previous:
                try:
                    await previous.aclose()
                except Exception:
                    logger.warning('关闭旧主聊天 provider client 失败', exc_info=True)
        except Exception as exc:
            if replacement:
                try:
                    await replacement.aclose()
                except Exception:
                    pass
            logger.error('切换主聊天模型失败: profile=%s', profile.id, exc_info=True)
            raise HTTPException(status_code=500, detail='主聊天模型切换失败，当前模型未改变') from exc
    return {'success': True, **public_main_model_state(profile.id)}
