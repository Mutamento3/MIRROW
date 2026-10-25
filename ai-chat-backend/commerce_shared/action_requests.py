"""Durable user-input requests used by address/contact checkpoints.

The notification envelope carries only a request id.  Sensitive form values
are local action state and are never copied into a AI-facing result or a log.
Resolution is idempotent and survives frontend reloads.
"""

from __future__ import annotations

import json
import sqlite3
import asyncio
import inspect
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ActionRequest:
    request_id: str
    kind: str
    status: str
    fields: tuple[str, ...]
    created_at: str
    resolved_at: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "status": self.status,
            "fields": list(self.fields),
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "expires_at": self.expires_at,
        }


class ActionRequestTerminal(RuntimeError):
    """A terminal action request cannot be resolved again."""


class ActionRequestStore:
    """Small SQLite store for durable form requests.

    ``payload`` is intentionally local-only.  ``get`` never returns it unless
    the caller explicitly asks for ``include_payload=True`` from trusted
    domain code.  Even then the normal model/notification paths do not use it.
    """

    def __init__(self, db_path: str | Path = "data/commerce.db", notifier: Callable[[dict[str, Any]], Any] | None = None):
        self.db_path = Path(db_path)
        self.notifier = notifier
        self.initialize()

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS commerce_action_requests (
                    request_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    response_json TEXT NOT NULL DEFAULT '',
                    expires_at TEXT
                )
            """)
            try:
                conn.execute("ALTER TABLE commerce_action_requests ADD COLUMN expires_at TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    _PRIVATE_KEY_PARTS = (
        "address", "place", "detail", "phone", "mobile", "name", "contact",
        "password", "token", "secret",
    )

    _PAYLOAD_SCHEMA: dict[str, frozenset[str]] = {
        "delivery_address": frozenset({"task_id", "contact_hint"}),
        "contact_confirmation": frozenset({"task_id", "warning", "masked_preview", "recognized_rows"}),
        "meituan_contact_confirmation": frozenset({"task_id", "warning", "masked_preview", "recognized_rows"}),
        "generic": frozenset({"task_id", "status"}),
    }
    _RESPONSE_SCHEMA: dict[str, frozenset[str]] = {
        "delivery_address": frozenset({"confirmed", "cancelled", "status", "fields", "reason", "address_label", "location_category"}),
        "contact_confirmation": frozenset({"confirmed", "cancelled", "status", "fields", "reason", "masked_preview"}),
        "meituan_contact_confirmation": frozenset({"confirmed", "cancelled", "status", "fields", "reason", "masked_preview"}),
        "generic": frozenset({"confirmed", "cancelled", "status", "fields", "reason"}),
    }

    @classmethod
    def _safe_metadata_value(cls, key: str, value: Any, *, depth: int = 0) -> Any:
        """Keep only non-sensitive, bounded metadata for the durable row."""
        if any(part in str(key).lower() for part in cls._PRIVATE_KEY_PARTS):
            return None
        if depth > 4:
            return None
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for child_key, child_value in value.items():
                safe = cls._safe_metadata_value(str(child_key), child_value, depth=depth + 1)
                if safe is not None:
                    result[str(child_key)[:80]] = safe
            return result
        if isinstance(value, (list, tuple)):
            result = []
            for child in value[:20]:
                safe = cls._safe_metadata_value("item", child, depth=depth + 1)
                if safe is not None:
                    result.append(safe)
            return result
        if isinstance(value, (str, int, float, bool)) or value is None:
            return str(value)[:500] if isinstance(value, str) else value
        return None

    @classmethod
    def _safe_metadata(cls, value: Mapping[str, Any] | None) -> dict[str, Any]:
        safe = cls._safe_metadata_value("root", dict(value or {}))
        return safe if isinstance(safe, dict) else {}

    @classmethod
    def _schema_metadata(cls, kind: str, value: Mapping[str, Any] | None, *, response: bool = False) -> dict[str, Any]:
        """Allow only known, bounded metadata fields for this action kind."""
        allowed = (cls._RESPONSE_SCHEMA if response else cls._PAYLOAD_SCHEMA).get(kind, cls._PAYLOAD_SCHEMA["generic"])
        source = dict(value or {})
        result: dict[str, Any] = {}
        for key in allowed:
            if key not in source:
                continue
            item = source[key]
            if key in {"confirmed", "cancelled"}:
                if isinstance(item, bool):
                    result[key] = item
            elif key in {"fields", "recognized_rows"}:
                if isinstance(item, (list, tuple)):
                    result[key] = [cls._safe_display_text(str(child)[:120]) for child in item[:20] if isinstance(child, (str, int, float, bool))]
            elif isinstance(item, (str, int, float, bool)) or item is None:
                if isinstance(item, str) and key in {"masked_preview", "contact_hint"}:
                    result[key] = cls._safe_display_text(item)
                else:
                    result[key] = str(item)[:500] if isinstance(item, str) else item
        return result

    @staticmethod
    def _safe_display_text(value: str) -> str:
        """Keep masked UI hints useful without persisting contact/address data."""
        text = " ".join(str(value or "").split())[:180]
        text = re.sub(r"(?<!\d)(1\d{2})[\s-]?\d{4}[\s-]?(\d{4})(?!\d)", r"\1****\2", text)
        # Contact hints are category labels, never free-form addresses.
        lowered = text.strip().lower()
        if lowered in {"home", "work", "default", "elsewhere", "家", "公司", "默认", "其他"}:
            return text
        if any(marker in text for marker in ("省", "市", "区", "县", "路", "街", "号", "室", "栋", "单元")) and any(ch.isdigit() for ch in text):
            return "[地址已脱敏]"
        return text

    @staticmethod
    def _safe_response(value: Mapping[str, Any] | None) -> dict[str, Any]:
        """Persist only non-sensitive action outcome metadata.

        Address/contact form fields are consumed by the host adapter in
        process memory.  They must not become ordinary SQLite JSON even when
        a caller accidentally includes them in a resolve payload.
        """
        return ActionRequestStore._safe_metadata(value)

    def create(self, kind: str, *, fields: list[str] | tuple[str, ...] = (), payload: Mapping[str, Any] | None = None, request_id: str | None = None, ttl_seconds: int | float = 300) -> ActionRequest:
        request_id = str(request_id or uuid4().hex)
        kind = str(kind or "user_input")[:80]
        fields = tuple(str(item)[:50] for item in fields if str(item).strip())
        now = _now()
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM commerce_action_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                safe_payload = self._schema_metadata(kind, payload)
                try:
                    ttl = max(1, min(86400, int(float(ttl_seconds))))
                except (TypeError, ValueError):
                    ttl = 300
                expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
                conn.execute("INSERT INTO commerce_action_requests(request_id,kind,fields_json,payload_json,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?)", (request_id, kind, self._json(fields), self._json(safe_payload), "pending", now, expires_at))
                row = conn.execute("SELECT * FROM commerce_action_requests WHERE request_id=?", (request_id,)).fetchone()
        action = self._decode(row)
        envelope = {
            "type": "action_request",
            "request_id": action.request_id,
            "kind": action.kind,
            "fields": list(action.fields),
            "status": action.status,
        }
        if self.notifier is not None:
            result = self.notifier(envelope)
            # Keep this helper sync/async friendly without forcing a specific
            # notification implementation on domain packages.
            if hasattr(result, "__await__"):
                try:
                    asyncio.get_running_loop().create_task(result)
                except RuntimeError:
                    # Do not leave a coroutine un-awaited in synchronous
                    # callers; notification delivery is optional here.
                    close = getattr(result, "close", None)
                    if close:
                        close()
        return action

    async def create_async(self, kind: str, *, fields: list[str] | tuple[str, ...] = (), payload: Mapping[str, Any] | None = None, request_id: str | None = None, ttl_seconds: int | float = 300) -> ActionRequest:
        action = self.create(kind, fields=fields, payload=payload, request_id=request_id, ttl_seconds=ttl_seconds)
        if self.notifier is not None:
            # ``create`` already called a sync notifier.  Async callers may
            # supply an awaitable notifier by using this method; it is invoked
            # again only for awaitables is not desirable, so keep notifier
            # callbacks sync in the default implementation.
            pass
        return action

    def _decode(self, row: sqlite3.Row | None) -> ActionRequest:
        if row is None:
            raise KeyError("action_request_not_found")
        try:
            fields = tuple(json.loads(row["fields_json"] or "[]"))
        except Exception:
            fields = ()
        expires_at = row["expires_at"] if "expires_at" in row.keys() else None
        return ActionRequest(row["request_id"], row["kind"], row["status"], fields, row["created_at"], row["resolved_at"], expires_at)

    def _expire(self, request_id: str) -> None:
        now = datetime.now(timezone.utc)
        with self._connection() as conn:
            row = conn.execute("SELECT status,expires_at FROM commerce_action_requests WHERE request_id=?", (str(request_id or ""),)).fetchone()
            if row is None or row["status"] != "pending" or not row["expires_at"]:
                return
            try:
                expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
            except ValueError:
                expires = now
            if expires <= now:
                conn.execute("UPDATE commerce_action_requests SET status='expired',resolved_at=?,payload_json='{}',response_json='{}' WHERE request_id=? AND status='pending'", (_now(), str(request_id or "")))

    def get(self, request_id: str, *, include_payload: bool = False) -> dict[str, Any] | None:
        self._expire(request_id)
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM commerce_action_requests WHERE request_id=?", (str(request_id or ""),)).fetchone()
        if row is None:
            return None
        action = self._decode(row).to_dict()
        if include_payload:
            try:
                action["payload"] = json.loads(row["payload_json"] or "{}")
            except Exception:
                action["payload"] = {}
        else:
            action["payload_available"] = bool(row["payload_json"] and row["payload_json"] != "{}")
        if row["response_json"]:
            try:
                action["response"] = json.loads(row["response_json"])
            except Exception:
                action["response"] = {}
        return action

    def resolve(self, request_id: str, response: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Resolve exactly once; replaying a terminal request is rejected."""
        request_id = str(request_id or "")
        self._expire(request_id)
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM commerce_action_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError("action_request_not_found")
            if row["status"] != "pending":
                raise ActionRequestTerminal("action_request_not_pending")
            now = _now()
            safe_response = self._schema_metadata(str(row["kind"]), response, response=True)
            conn.execute("UPDATE commerce_action_requests SET status='resolved',resolved_at=?,response_json=? WHERE request_id=? AND status='pending'", (now, self._json(safe_response), request_id))
        return self.get(request_id) or {}

    def cancel(self, request_id: str, *, reason: str = "cancelled") -> dict[str, Any]:
        request_id = str(request_id or "")
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM commerce_action_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError("action_request_not_found")
            if row["status"] == "pending":
                conn.execute("UPDATE commerce_action_requests SET status='cancelled',resolved_at=?,response_json=? WHERE request_id=? AND status='pending'", (_now(), self._json({"reason": str(reason or "cancelled")[:200]}), request_id))
        return self.get(request_id) or {}

    def list_pending(self, kind: str | None = None) -> list[dict[str, Any]]:
        # Expire pending rows before presenting a reloadable form list.
        with self._connection() as conn:
            rows = conn.execute("SELECT request_id FROM commerce_action_requests WHERE status='pending'").fetchall()
        for row in rows:
            self._expire(row["request_id"])
        query = "SELECT * FROM commerce_action_requests WHERE status='pending'"
        params: tuple[Any, ...] = ()
        if kind:
            query += " AND kind=?"
            params = (str(kind),)
        query += " ORDER BY created_at"
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode(row).to_dict() for row in rows]


__all__ = ["ActionRequest", "ActionRequestStore", "ActionRequestTerminal"]
