from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping

from .config import Settings
from .policy import POLICIES, POLICY_BY_NAME, REVIEWED_PRIVATE_ADDITIONS, TablePolicy
from .redaction import sanitize_row


@dataclass(frozen=True)
class SearchHit:
    policy: str
    identity: dict[str, Any]
    title: str


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class ProjectionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._reads = asyncio.Semaphore(settings.max_concurrent_reads)

    def _db_path(self, policy: TablePolicy) -> Path:
        candidate = (self.settings.backend_root / policy.database).resolve()
        root = self.settings.backend_root.resolve()
        if not candidate.is_relative_to(root):
            raise RuntimeError("database policy escaped the backend root")
        return candidate

    def _open(self, policy: TablePolicy) -> sqlite3.Connection:
        path = self._db_path(policy)
        if not path.is_file():
            raise FileNotFoundError(policy.database)
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=self.settings.sqlite_busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute(f"PRAGMA busy_timeout={self.settings.sqlite_busy_timeout_ms}")
        return connection

    @staticmethod
    def _check_schema(connection: sqlite3.Connection, policy: TablePolicy) -> None:
        actual = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({_quote(policy.table)})"))
        reviewed_private = REVIEWED_PRIVATE_ADDITIONS.get(policy.name, frozenset())
        if tuple(column for column in actual if column not in reviewed_private) != policy.expected_columns:
            raise RuntimeError(f"schema_pending_review:{policy.name}")

    @staticmethod
    def _install_read_guard(connection: sqlite3.Connection, deadline: float) -> None:
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}

        def authorize(action: int, _arg1: str | None, _arg2: str | None, _db: str | None, _source: str | None) -> int:
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

        def progress() -> int:
            return 1 if time.monotonic() > deadline else 0

        connection.set_authorizer(authorize)
        connection.set_progress_handler(progress, 500)

    def _status_sync(self, policy: TablePolicy) -> dict[str, Any]:
        try:
            connection = self._open(policy)
            try:
                self._check_schema(connection, policy)
            finally:
                connection.close()
            return {"name": policy.name, "description": policy.description, "status": "available"}
        except FileNotFoundError:
            return {"name": policy.name, "description": policy.description, "status": "unavailable"}
        except RuntimeError as exc:
            if str(exc).startswith("schema_pending_review:"):
                return {"name": policy.name, "description": policy.description, "status": "pending_review"}
            raise

    async def list_domains(self) -> list[dict[str, Any]]:
        async with self._reads:
            return await asyncio.to_thread(lambda: [self._status_sync(policy) for policy in POLICIES])

    def _query_sync(
        self,
        policy: TablePolicy,
        filters: Mapping[str, Any],
        contains: str | None,
        limit: int,
        timeout_ms: int | None = None,
        identity_only: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        connection = self._open(policy)
        try:
            self._check_schema(connection, policy)
            deadline = time.monotonic() + (timeout_ms or self.settings.query_timeout_ms) / 1000
            self._install_read_guard(connection, deadline)
            columns = tuple(dict.fromkeys(policy.visible_columns + policy.id_columns))
            clauses: list[str] = []
            parameters: list[Any] = []
            selected_filters = identity_only if identity_only is not None else filters
            allowed_filters = set(policy.visible_columns + policy.id_columns)
            for key, value in selected_filters.items():
                if key not in allowed_filters or isinstance(value, (dict, list, tuple, set)):
                    raise ValueError(f"filter not allowed: {key}")
                clauses.append(f"{_quote(key)} IS ?" if value is None else f"{_quote(key)} = ?")
                parameters.append(value)
            if contains:
                if identity_only is not None or not policy.text_columns:
                    raise ValueError("text search is not available for this projection")
                escaped = contains.replace("!", "!!").replace("%", "!%").replace("_", "!_")
                clauses.append("(" + " OR ".join(f"{_quote(column)} LIKE ? ESCAPE '!'" for column in policy.text_columns) + ")")
                parameters.extend([f"%{escaped}%"] * len(policy.text_columns))
            sql = f"SELECT {', '.join(_quote(column) for column in columns)} FROM {_quote(policy.table)}"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            if policy.order_by:
                sql += f" ORDER BY {_quote(policy.order_by)} DESC"
            sql += " LIMIT ?"
            parameters.append(limit)
            rows = []
            for record in connection.execute(sql, parameters):
                raw = dict(record)
                visible = {column: raw.get(column) for column in policy.visible_columns}
                identity = {column: raw.get(column) for column in policy.id_columns}
                rows.append({"data": sanitize_row(visible), "identity": identity})
            return rows
        finally:
            connection.close()

    async def query(
        self,
        domain: str,
        filters: Mapping[str, Any] | None = None,
        contains: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        policy = POLICY_BY_NAME.get(domain)
        if policy is None:
            raise ValueError("unknown domain")
        limit = max(1, min(int(limit), self.settings.max_rows))
        contains = contains.strip()[:160] if contains else None
        async with self._reads:
            rows = await asyncio.to_thread(self._query_sync, policy, filters or {}, contains, limit)
        public_rows = [row["data"] for row in rows]
        encoded = json.dumps(public_rows, ensure_ascii=False, default=str).encode("utf-8")
        if len(encoded) > self.settings.max_response_bytes:
            raise RuntimeError("response_budget_exceeded")
        return public_rows

    async def fetch(self, domain: str, identity: Mapping[str, Any]) -> dict[str, Any]:
        policy = POLICY_BY_NAME.get(domain)
        if policy is None or set(identity) != set(policy.id_columns):
            raise ValueError("invalid source identity")
        async with self._reads:
            rows = await asyncio.to_thread(self._query_sync, policy, {}, None, 1, None, identity)
        if not rows:
            raise ValueError("source not found")
        return rows[0]["data"]

    async def search(self, query: str, limit: int = 12) -> list[SearchHit]:
        query = query.strip()[:160]
        if not query:
            return []
        hits: list[SearchHit] = []
        started = time.monotonic()
        for policy in POLICIES:
            if not policy.text_columns or len(hits) >= limit or time.monotonic() - started > 0.8:
                continue
            try:
                async with self._reads:
                    rows = await asyncio.to_thread(self._query_sync, policy, {}, query, min(3, limit - len(hits)), 80)
            except (sqlite3.Error, RuntimeError, FileNotFoundError):
                continue
            for row in rows:
                data = row["data"]
                title_value = next((data.get(column) for column in policy.text_columns if data.get(column)), policy.name)
                title = str(title_value).replace("\n", " ")[:120]
                hits.append(SearchHit(policy.name, row["identity"], title))
        return hits[:limit]
