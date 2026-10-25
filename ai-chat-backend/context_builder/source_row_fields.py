"""Pure row helpers shared by retained source projections."""
import json
from collections.abc import Mapping

def calls(row):
    raw = row.get('tool_calls') or row.get('toolCalls') or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []

def message_id(row):
    return str(row.get('message_id') or row.get('id') or '')
