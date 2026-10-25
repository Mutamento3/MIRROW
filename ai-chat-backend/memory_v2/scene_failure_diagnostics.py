"""Fixed scene failure categories; no source bodies or provider prose."""
import json
import sqlite3

from .scene_regrouping_output import FORMAT_CODES

_LOCAL_CODES = frozenset({
    'scene_plan_contract', 'scene_plan_window_contract', 'scene_plan_source_partition',
    'scene_plan_incomplete', 'scene_plan_session_boundary', 'scene_plan_explicit_boundary',
    'scene_segment_contract', 'scene_segment_missing_previous', 'scene_segment_count',
    'scene_segment_fields', 'scene_segment_source_partition', 'scene_segment_incomplete',
    'scene_reconstruction_items', 'scene_reconstruction_item_contract',
    'scene_reconstruction_summary_evidence', 'scene_reconstruction_source_partition',
    'scene_reconstruction_session_boundary', 'scene_reconstruction_source_changed',
    'scene_reconstruction_open_day', 'scene_reconstruction_retry_backoff',
    'scene_reconstruction_provider_failed', 'scene_reconstruction_json_invalid',
    'scene_reconstruction_sqlite_error', 'scene_reconstruction_unclassified',
    'scene_regroup_open_day', 'scene_regroup_existing_boundary',
}) | FORMAT_CODES
_STEPS = frozenset({'plan', 'segments', 'regroup'})
_STAGES = frozenset({'cache_read', 'claim', 'provider', 'json_decode', 'validate', 'cache_write'})


def safe_scene_error_code(value):
    if not isinstance(value, str):
        return ''
    if value in _LOCAL_CODES:
        return value
    prefix = 'scene_reconstruction_'
    if value.startswith(prefix):
        from .day_settlement_runner import safe_day_settlement_provider_error
        if safe_day_settlement_provider_error(value[len(prefix):]):
            return value
    return ''


def failure_report(report, exception):
    code = safe_scene_error_code(str(exception)) if isinstance(exception, ValueError) else ''
    if not code:
        code = ('scene_reconstruction_json_invalid' if isinstance(exception, json.JSONDecodeError) else
                'scene_reconstruction_sqlite_error' if isinstance(exception, sqlite3.Error) else
                'scene_reconstruction_unclassified')
    return {**report, 'status': 'error', 'error_code': code}


def project_scene_failure(scene):
    if not isinstance(scene, dict):
        return {}
    code = safe_scene_error_code(scene.get('error_code'))
    if not code:
        return {}
    count = scene.get('request_count')
    result = {'error_code': code, 'request_count': count if type(count) is int and count >= 0 else 0}
    for key, allowed in (('failure_step', _STEPS), ('failure_stage', _STAGES)):
        value = scene.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    return result
