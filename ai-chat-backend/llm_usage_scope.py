"""Task-local, body-free usage labels for injected LLM callables."""
from contextlib import contextmanager
from contextvars import ContextVar
import re

_scope = ContextVar("llm_usage_scope", default="")
_metadata = ContextVar("llm_usage_metadata", default=None)
_FIELDS = {
    'source_date': r'\d{4}-\d{2}-\d{2}',
    'cognition_run_id': r'[0-9a-f]{64}',
    'cognition_daily_run_id': r'[0-9a-f]{64}',
    'cognition_request_hash': r'[0-9a-f]{64}',
    'cognition_source_hash': r'[0-9a-f]{64}',
    'cognition_material_hash': r'[0-9a-f]{64}',
    'cognition_attempt_id': r'[0-9a-f]{32}',
    'cognition_call_id': r'[0-9a-f]{32}',
    'cognition_lane': r'(world|self|interest|other)',
    'cognition_stage': r'((world|maintenance|self|interest|other|subjective)_(extract|review)|habit_extract|habit_report_repair|reference_review|brief_projection|brief_review)',
    'material_prompt_mode': r'(baseline|materials_v1)',
}


@contextmanager
def usage_metadata_scope(**values):
    safe = {key: value for key, value in values.items()
            if key in _FIELDS and isinstance(value, str) and re.fullmatch(_FIELDS[key], value)}
    token = _metadata.set({**usage_metadata(), **safe})
    try:
        yield
    finally:
        _metadata.reset(token)


def usage_metadata():
    return dict(_metadata.get() or {})


@contextmanager
def usage_scope(label: str):
    token = _scope.set(label)
    try:
        yield
    finally:
        _scope.reset(token)


def resolve_usage_tag(tag: str) -> str:
    if tag in {"flash_internal", "flash_text", "flash_long_text"}:
        return _scope.get() or tag
    return tag
