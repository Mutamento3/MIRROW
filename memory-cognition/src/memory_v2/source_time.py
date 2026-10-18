"""Authority instants: legacy naive timestamps are local Asia/Shanghai."""
from datetime import datetime, timedelta, timezone


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.astimezone(timezone.utc)
