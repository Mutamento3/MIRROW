"""Reviewed two-station candidate setup; no keys or generation requests."""
from urllib.parse import urlsplit
from .config import ConfigError, ModelSource, Station, validate_config
from .policies import CategoryPolicy

SMALL_NODES = ['intent_analysis', 'behavior_tool_compiler', 'sentinel_activity_summary', 'habit_radar_shadow', 'ambient_listening']


def cost_ranked_policies(config, *, new_key_group=None, flash_id='relay55_flash',
                         internal_id='relay55_gemini3', lite_id='relaynew_gemini_lite'):
    """Reviewed relay order; no guessed Key discounts or paid probes.

    Keep model contracts, user overrides and station credentials. Main's warm
    official route stays unchanged until a compatible cheaper Pro is verified.
    This is an explicit setup operation, never a read-time price migration.
    """
    updated = config.model_copy(deep=True)
    known = {model.id for station in updated.stations for model in station.models}
    required = {flash_id, internal_id, 'direct_flash'}
    if not required <= known:
        raise ConfigError('请先添加已核实的 55 按量模型与官方回退')
    internal = [internal_id, flash_id]
    if new_key_group == 'gemini' and lite_id in known:
        internal.append(lite_id)
    internal.append('direct_flash')
    # Qwen's 1.8/10.8 CNY rates exceed the official valley tariff. Its tiny
    # peak-only advantage disappears with cache/output; not a general fallback.
    for category, chain in (('high', [flash_id, 'direct_flash']), ('low', internal)):
        policy = updated.policies.get(category, CategoryPolicy()).model_copy(deep=True)
        policy.constant = list(chain)
        policy.peak = list(chain)
        policy.valley = list(chain)
        policy.peak_enabled = False
        updated.policies[category] = policy
    # 55's short-JSON failures also incur official recovery cost. Retain the
    # known working 512-token contract without repeatedly paying both sources.
    updated.overrides.setdefault('sentinel_first_layer', ['direct_flash'])
    validate_config(updated)
    return updated


def two_station_preset(config):
    updated = config.model_copy(deep=True)

    def station_for(hosts, identifier, name, base, key_env):
        existing = [site for site in updated.stations if urlsplit(site.base_url).hostname in hosts]
        if len(existing) > 1:
            raise ConfigError('同一候选站存在多条来源，请先确认要使用的站点')
        if existing:
            site = existing[0]
            if site.protocol != 'openai-chat':
                raise ConfigError('候选站需使用已核实的 Chat 兼容协议')
            return site
        if any(site.id == identifier for site in updated.stations):
            raise ConfigError('候选站预设 ID 已被其他站点使用')
        site = Station(id=identifier, name=name, base_url=base, protocol='openai-chat', key_env=key_env)
        updated.stations.append(site)
        return site

    def add(site, identifier, label, model, profile, roles, allowed_nodes=None):
        existing = next((item for item in site.models if item.id == identifier or item.model == model), None)
        if existing:
            existing.model = model
            existing.profile = profile
            existing.roles = roles
            existing.allowed_nodes = allowed_nodes or []
            return existing.id
        if any(item.id == identifier for station in updated.stations for item in station.models):
            raise ConfigError('候选模型预设 ID 已被其他来源使用')
        site.models.append(ModelSource(id=identifier, label=label, model=model, profile=profile,
            json_mode='json_object', thinking='default', roles=roles, allowed_nodes=allowed_nodes or []))
        return identifier

    relay55 = station_for({'5w5.wtf','api.55.al'},'relay55','55 · 按量（default 分组）',
        'https://5w5.wtf/v1','MIRROW_RELAY_KEY_RELAY55')
    relay_new = station_for({'127.0.0.1'},'relay_new','NEW-便宜站 · 按量（Key 选 gemini 分组）',
        'http://127.0.0.1:3010/v1','MIRROW_RELAY_KEY_RELAYNEW')
    pro = add(relay55,'relay55_pro','DS Pro · 候选', '未知[none-量-0.25x]deepseek-v4-pro-0813','deepseek',['main'])
    flash = add(relay55,'relay55_flash','DS V4.1 Flash · 对外表达', '未知[none-量-0.25x]deepseek-v4.1-flash','deepseek',['expression','internal'])
    gemini = add(relay55,'relay55_gemini3','Gemini 3 Flash · 内部整理', '逆[Ag1-量-0.2x]gemini-3-flash','gemini',['internal'])
    lite = add(relay_new,'relaynew_gemini_lite','Gemini 3.1 Flash-Lite · 小结构', 'gemini-3.1-flash-lite','gemini',['internal'],SMALL_NODES)
    # Main keeps its warm official prefix; high never switches by time. The
    # unverified Pro candidate is available in settings, not silently promoted.
    updated.policies['main'] = CategoryPolicy(constant=['direct_pro'])
    updated.policies['high'] = CategoryPolicy(constant=[flash,'direct_flash'])
    updated.policies['low'] = CategoryPolicy(constant=[gemini,flash,'direct_flash'],
        peak=[gemini,flash,'direct_flash'], valley=[gemini,flash,'direct_flash'])
    # Existing exceptions remain authoritative. No tool or memory material is
    # changed, and adding the Lite backup never makes it a memory/day-end model.
    # Newly registered Keys have no verified account group yet. Lite remains
    # available for explicit setup after confirming its gemini multiplier.
    updated = cost_ranked_policies(updated, flash_id=flash, internal_id=gemini, lite_id=lite)
    return updated, {'pro_candidate':pro,'expression':flash,'internal':gemini,'small_backup':lite}


def day_end_preset(config):
    """Explicit verified relay setup; no credential changes or paid probes.

    Alias availability was checked with synthetic short/long evidence on
    2026-10-14. This is never applied automatically while reading settings.
    Per-call NEW is visible but not admitted before its separate Key is tested.
    """
    updated = config.model_copy(deep=True)
    site55 = next((s for s in updated.stations if s.id == 'relay55'), None)
    new = next((s for s in updated.stations if s.id == 'relay_new'), None)
    if site55 is None or new is None:
        raise ConfigError('请先配置 55 和 NEW 站点')
    primary = next((m for m in site55.models if m.id == 'relay55_gemini3'), None)
    if primary is None:
        primary = ModelSource(id='relay55_gemini3', label='Gemini 3 Flash · 内部整理',
            model='逆[Ag1-量-0.2x]gemini-3-flash', profile='gemini', json_mode='prompt',
            sampling=False, roles=['internal'])
        site55.models.append(primary)
    old = next((m for m in site55.models if m.id == 'relay55_gemini_high'), None)
    if old:
        old.enabled = False  # keep the failed alias and its real audit history visible
        old.label = 'Gemini 3.7 Flash high · 旧失效线路（已停用）'
    def replace_old(chain):
        return [primary.id if key == 'relay55_gemini_high' else key for key in chain]
    for collection in (updated.chains, updated.overrides):
        for key, chain in collection.items():
            collection[key] = list(dict.fromkeys(replace_old(chain)))
    for policy in updated.policies.values():
        for key in ('constant', 'peak', 'valley'):
            chain = getattr(policy, key)
            if chain is not None:
                setattr(policy, key, list(dict.fromkeys(replace_old(chain))))
    primary.label = 'Gemini 3 Flash · 内部整理（合成质检通过）'
    primary.profile, primary.json_mode, primary.sampling = 'gemini', 'prompt', False
    routes = ['daily_summary', 'cognition_brief_review', 'cognition_maintenance', 'memory_encoding']
    backup = next((m for m in new.models if m.id == 'relaynew_gemini25_flash'), None)
    if backup is None:
        backup = ModelSource(id='relaynew_gemini25_flash', label='Gemini 2.5 Flash · 日结跨站备用',
            model='gemini-2.5-flash-nothinking', profile='gemini', json_mode='prompt', sampling=False,
            roles=['internal'], allowed_nodes=routes)
        new.models.append(backup)
    backup.allowed_nodes = list(dict.fromkeys([*backup.allowed_nodes, 'cognition_brief_review']))
    for route in routes:
        updated.overrides[route] = [primary.id, backup.id, 'direct_flash']
    # Natural 10/14 review failed the identity/point coverage and total-budget
    # contract even after one concrete correction. Use the independent relay
    # for this stage, keeping cheap extraction/generation on the first station.
    updated.overrides['cognition_brief_review'] = [backup.id, 'direct_flash']
    if not any(s.id == 'relay_new_count' for s in updated.stations):
        updated.stations.append(Station(id='relay_new_count', name='NEW · 按次（另填 Key，待质检）',
            base_url=new.base_url, protocol=new.protocol, key_env='MIRROW_RELAY_KEY_RELAYNEWCOUNT',
            models=[ModelSource(id='relaynew_count25_flash', label='Gemini 2.5 Flash · 按次待质检',
                model='a/gemini-2.5-flash-nothinking', profile='gemini', json_mode='prompt', sampling=False,
                roles=['internal'], allowed_nodes=routes, enabled=False)]))
    for site in updated.stations:
        if site.id == 'relay_new_count':
            for model in site.models:
                if model.id == 'relaynew_count25_flash':
                    model.allowed_nodes = list(dict.fromkeys([*model.allowed_nodes, 'cognition_brief_review']))
    validate_config(updated)
    return updated
