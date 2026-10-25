"""Source-backed activity summary. Failed compression never truncates evidence."""
import asyncio
from datetime import datetime
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


def source_hashes(rows):
    return {str(row["id"]): hashlib.sha256(json.dumps(
        row, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for row in rows}


def timeline(rows):
    return " → ".join(
        f"{(r['session_start'] or '')[11:16]}-{(r['session_end'] or '')[11:16]} "
        f"{'PC' if r['platform'] == 'pc' else '手机'}{r['app_name']}"
        f"({(r['duration_seconds'] or 0) // 60}min)" for r in rows)


def valid_success(success, hashes):
    return (isinstance(success, dict) and bool(success.get("text"))
            and isinstance(success.get("sources"), dict) and bool(success["sources"])
            and all(hashes.get(key) == value for key, value in success["sources"].items()))


def render(rows, success):
    hashes = source_hashes(rows)
    if not valid_success(success, hashes):
        return timeline(rows)
    uncovered = [r for r in rows if str(r["id"]) not in success["sources"]]
    return success["text"] + ("\n未纳入上次成功摘要的已完成设备会话：" + timeline(uncovered)
                              if uncovered else "")


def source_rows(store, day):
    return [dict(row) for row in store._get_conn().execute(
        "SELECT id, app_name, platform, session_start, session_end, duration_seconds "
        "FROM app_sessions WHERE date(session_start)=? ORDER BY session_start,id", (day,))]


def saved_success(row):
    if not row:
        return None
    try:
        evidence = json.loads(row["field_evidence"] or "{}").get("behavior_chain") or {}
        return evidence.get("last_success") if evidence.get("value") == row["behavior_chain"] else None
    except (TypeError, ValueError, AttributeError):
        return None


def project_summary(store, row):
    """Validate persisted coverage at read time; no LLM and no storage writes."""
    success = saved_success(row)
    rows = source_rows(store, row["date"])
    if not valid_success(success, source_hashes(rows)):
        success = None
    text = render(rows, success)
    if text != (row.get("behavior_chain") or ""):
        row = dict(row)
        row["behavior_chain"] = text
        try:
            evidence = json.loads(row.get("field_evidence") or "{}")
            if not isinstance(evidence, dict):
                evidence = {}
        except (TypeError, ValueError):
            evidence = {}
        evidence["behavior_chain"] = {"value": text, "source": "app_sessions", "last_success": success}
        row["field_evidence"] = json.dumps(evidence, ensure_ascii=False)
    return row


class BehaviorChain:
    def __init__(self, store, call_llm):
        self.store, self.call_llm = store, call_llm
        self._lock = asyncio.Lock()

    def _rows(self, day):
        return source_rows(self.store, day)

    def _saved(self, day):
        row = self.store._get_conn().execute(
            "SELECT behavior_chain, field_evidence FROM daily_health_summary WHERE date=?", (day,)
        ).fetchone()
        if not row:
            return "", None
        return row["behavior_chain"], saved_success(row)

    async def refresh(self, day):
        # One writer, even when a slow route overlaps the next scheduled refresh.
        async with self._lock:
            rows = self._rows(day)
            old_text, success = self._saved(day)
            hashes = source_hashes(rows)
            if not valid_success(success, hashes):
                success = None
            if success and success["sources"] == hashes:
                text = render(rows, success)
                if text != old_text:
                    self.store.upsert_daily_summary(day, behavior_chain=text, _field_evidence={
                        "behavior_chain": {"value": text, "source": "app_sessions", "last_success": success}
                    })
                return text
            raw = timeline(rows)
            chain = raw if len(raw) < 200 else ""
            if len(raw) >= 200:
                try:
                    result = await self.call_llm([{"role": "user", "content": (
                        '将以下设备使用时间线压缩为一段自然语言描述（40-80字）。'
                        '用“人类伙伴”开头，第三人称，捕捉时间段、App与节奏模式。输出一段话。\n时间线：' + raw
                    )}])
                    chain = result if isinstance(result, str) else (result or {}).get("content", "")
                    chain = chain.strip() if isinstance(chain, str) else ""
                except Exception as exc:
                    # The route owns its bounded timeout/retry budget; no 20s outer cancellation.
                    logger.warning("Behavior summary unavailable: %s", type(exc).__name__)
            if chain:
                success = {"text": chain, "sources": hashes, "generated_at": datetime.now().isoformat()}
            # Sessions can finish, change or be deleted during generation.
            current = self._rows(day)
            if not valid_success(success, source_hashes(current)):
                success = None
            text = render(current, success)
            if text != old_text or chain:
                self.store.upsert_daily_summary(day, behavior_chain=text, _field_evidence={
                    "behavior_chain": {"value": text, "source": "app_sessions", "last_success": success}
                })
            return text or None
