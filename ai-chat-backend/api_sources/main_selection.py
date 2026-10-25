"""Select the configured AI main source for standalone, tool-free requests."""
import asyncio


async def main_source():
    from main_model_profiles import DEFAULT_PROFILE_ID, get_configured_profile_id, get_main_model_profile
    from api_sources.config import load_config, resolve_chain, ModelSource
    from behavior_scheduler import get_global_scheduler
    scheduler = await get_global_scheduler()
    config, env, _ = await asyncio.to_thread(load_config)
    selected = getattr(scheduler, 'main_model_profile_id', None) or get_configured_profile_id(env)
    if selected == DEFAULT_PROFILE_ID:
        chain, _ = await asyncio.to_thread(resolve_chain, 'main_chat')
        from api_sources.availability import readiness
        for source in chain:
            if source.get('api_key') and readiness(source, terminal=source.get('stable', False))['eligible']:
                return source, {'selection': selected}
    profile = get_main_model_profile(selected, env)
    if not profile.api_key:
        raise ValueError('当前底模尚未配置')
    for station in config.stations:
        for model in station.models:
            if station.base_url.rstrip('/') == profile.base_url.rstrip('/') and model.model == profile.model and station.enabled and model.enabled and env.get(station.key_env):
                return {'station': station.model_dump(), 'model': model.model_dump(), 'api_key': env[station.key_env], 'ordinal': 1}, {'selection': selected, 'manual': True}
    source = dict(station=dict(id=profile.station_id or 'main_' + profile.id, name=profile.station_name or profile.label,
                              base_url=profile.base_url, protocol='openai-chat', enabled=True),
                  model=ModelSource(id=profile.id, label=profile.label, model=profile.model,
                                    profile='gemini' if 'gemini' in profile.id else 'generic').model_dump(),
                  api_key=profile.api_key, ordinal=1)
    return source, {'selection': selected, 'manual': True}
