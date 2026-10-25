"""Commit model-switch facts only for a successful manual main-chat switch."""
from model_provenance import normalize_model_label


def record_switch(before, after):
    from event_chronicle import get_global_chronicle
    from shared_state import get_active_session_id
    from conversation_manager_v2 import resolve_active_date
    from time_utils import now_iso
    from uuid import uuid4
    before = normalize_model_label(before) or '未知'
    after = normalize_model_label(after) or '未知'
    if before == after:
        return
    session_id = get_active_session_id()
    if not session_id:
        raise RuntimeError('model_switch_requires_active_session')
    if not get_global_chronicle().add_conversation_event(
        active_date=resolve_active_date(session_id), session_id=session_id,
        timestamp=now_iso(), event_id='model_switch_' + uuid4().hex,
        event_type='model_switch',
        content=f'人类伙伴将你的主聊天回复模型从 {before} 切换为 {after}。后台任务仍使用各自配置，已开始的调用保留其原模型。',
    ):
        raise RuntimeError('model_switch_event_not_saved')


def activate_with_event(scheduler, replacement, profile, persist):
    """No awaits between pointer swap and event; restore selection if event fails."""
    old = scheduler.pro_adapter
    old_id = scheduler.main_model_profile_id
    old_label = scheduler.main_model_label
    old_vision = scheduler.main_model_supports_vision
    old_model = getattr(getattr(old, '_wrapper', None), 'model', '')
    persist(profile.id)
    try:
        scheduler.activate_main_model_adapter(replacement, profile_id=profile.id,
            label=profile.label, supports_vision=profile.supports_vision)
        record_switch(old_model, profile.model)
    except Exception:
        scheduler.activate_main_model_adapter(old, profile_id=old_id,
            label=old_label, supports_vision=old_vision)
        persist(old_id)
        raise
    return old
