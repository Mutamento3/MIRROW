"""Explicit one-source routes never inherit, append a fallback, or recover elsewhere."""
from .config import ENV_PATH, load_config
from .scope import current_snapshot
from .policies import supported_roles
from .availability import readiness


def resolve_single_source(route, *, require_ready=True, optional=False):
    from .catalog import SINGLE_SOURCE_NODES
    if route not in SINGLE_SOURCE_NODES:
        raise ValueError('route_not_single_source')
    config, env, revision = current_snapshot() or load_config(ENV_PATH)
    ids = config.overrides.get(route, [])
    if optional and route not in config.overrides:
        return None
    if len(ids) != 1:
        raise ValueError('single_source_override_required')
    found = [(s, m) for s in config.stations for m in s.models if m.id == ids[0]]
    if len(found) != 1:
        raise ValueError('single_source_missing')
    station, model = found[0]
    if require_ready and (not station.enabled or not model.enabled or 'internal' not in supported_roles(model.model_dump())
            or (model.allowed_nodes and route not in model.allowed_nodes)):
        raise ValueError('single_source_unavailable')
    source = {'station': station.model_dump(), 'model': model.model_dump(),
              'api_key': env.get(station.key_env, ''), 'ordinal': 1,
              'availability_managed': True, 'stable': False,
              'selection': {'revision': revision, 'period': 'override', 'inherited': False, 'managed': False}}
    if require_ready and not readiness(source)['eligible']:
        raise ValueError('single_source_not_ready')
    return source
