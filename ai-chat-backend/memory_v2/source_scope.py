"""Row-local source scope API; this public release has no separate fiction domain."""
from typing import Any, Mapping

def source_scope_ids(row: Mapping[str, Any]) -> tuple[str, ...]:
    return ()
