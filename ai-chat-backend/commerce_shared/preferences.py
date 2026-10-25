"""Shopping decisions and the bridge to the existing pending self-book flow.

Commerce choices are useful evidence for AI, but a single purchase is not a
durable personality edit.  This module therefore keeps an append-only audit
ledger and only *queues* a pending self-book candidate when a principle is
repeated or AI explicitly marks it as persistent.  The existing Wander
runtime store remains the owner of pending self-book candidates; this module
never writes self-book markdown directly.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _clean(value: Any, limit: int=800) -> str:
    return ' '.join(str(value or '').split())[:limit]
_PRIVATE_PARTS = ('password', 'token', 'secret', 'cookie', 'authorization', 'phone', 'mobile', 'address', 'name', 'credential', 'serial', 'ip')

def _safe(value: Any) -> Any:
    """Keep decision evidence compact and remove sensitive field families."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any((part in key_text.lower() for part in _PRIVATE_PARTS)):
                continue
            result[key_text[:80]] = _safe(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value[:30]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _clean(value, 600) if isinstance(value, str) else value
    return _clean(value, 600)

def _json(value: Any) -> str:
    return json.dumps(_safe(value), ensure_ascii=False, sort_keys=True, default=str)

@dataclass(frozen=True)
class ShoppingDecision:
    decision_id: str
    task_id: str
    merchant: str
    candidate_id: str
    choice: str
    reason: str
    evidence: tuple[Mapping[str, Any], ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {'decision_id': self.decision_id, 'task_id': self.task_id, 'merchant': self.merchant, 'candidate_id': self.candidate_id, 'choice': self.choice, 'reason': self.reason, 'evidence': [dict(item) for item in self.evidence], 'created_at': self.created_at}

class ShoppingDecisionLedger:
    """Append-only local ledger for every candidate selection.

    There is intentionally no update/delete method.  Replaying the same
    decision id is idempotent, while a different decision produces another
    immutable row so later preference reasoning can see the history.
    """

    def __init__(self, db_path: str | Path='data/commerce.db') -> None:
        self.db_path = Path(db_path)
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
            conn.execute('CREATE TABLE IF NOT EXISTS commerce_shopping_decisions (\n                    decision_id TEXT PRIMARY KEY,\n                    task_id TEXT NOT NULL,\n                    merchant TEXT NOT NULL,\n                    candidate_id TEXT NOT NULL,\n                    choice TEXT NOT NULL,\n                    reason TEXT NOT NULL,\n                    evidence_json TEXT NOT NULL,\n                    created_at TEXT NOT NULL\n                )')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_commerce_decisions_task ON commerce_shopping_decisions(task_id, created_at)')

    def append(self, *, task_id: str, merchant: str, candidate_id: str, choice: str, reason: str, evidence: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]=(), decision_id: str | None=None) -> ShoppingDecision:
        task_id, merchant = (_clean(task_id, 120), _clean(merchant, 30).lower())
        if merchant != 'meituan':
            raise ValueError('merchant_must_be_meituan')
        candidate_id, choice = (_clean(candidate_id, 160), _clean(choice, 80))
        if not task_id or not candidate_id:
            raise ValueError('shopping_decision_identity_required')
        decision_id = _clean(decision_id or uuid4().hex, 120)
        safe_evidence = tuple((_safe(dict(item)) for item in evidence or () if isinstance(item, Mapping)))
        created_at = _now()
        with self._connection() as conn:
            existing = conn.execute('SELECT * FROM commerce_shopping_decisions WHERE decision_id=?', (decision_id,)).fetchone()
            if existing is None:
                conn.execute('INSERT INTO commerce_shopping_decisions\n                       (decision_id,task_id,merchant,candidate_id,choice,reason,evidence_json,created_at)\n                       VALUES(?,?,?,?,?,?,?,?)', (decision_id, task_id, merchant, candidate_id, choice, _clean(reason, 1000), _json(safe_evidence), created_at))
                existing = conn.execute('SELECT * FROM commerce_shopping_decisions WHERE decision_id=?', (decision_id,)).fetchone()
        return self._decode(existing)
    record = append

    @staticmethod
    def _decode(row: sqlite3.Row) -> ShoppingDecision:
        try:
            raw = json.loads(row['evidence_json'] or '[]')
            evidence = tuple((item for item in raw if isinstance(item, Mapping)))
        except (TypeError, ValueError, json.JSONDecodeError):
            evidence = ()
        return ShoppingDecision(decision_id=str(row['decision_id']), task_id=str(row['task_id']), merchant=str(row['merchant']), candidate_id=str(row['candidate_id']), choice=str(row['choice']), reason=str(row['reason']), evidence=evidence, created_at=str(row['created_at']))

    def list(self, *, task_id: str | None=None, merchant: str | None=None) -> list[ShoppingDecision]:
        query = 'SELECT * FROM commerce_shopping_decisions WHERE 1=1'
        params: list[Any] = []
        if task_id:
            query += ' AND task_id=?'
            params.append(str(task_id))
        if merchant:
            query += ' AND merchant=?'
            params.append(str(merchant).lower())
        query += ' ORDER BY created_at, rowid'
        with self._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._decode(row) for row in rows]

def queue_pending_self_book_candidate(runtime_store: Any, *, run_id: str, activity_id: str, node_id: str, decision_id: str, statement: str, category: str='shopping_preference', keywords: list[str] | tuple[str, ...]=(), shopping_task_ids: list[str] | tuple[str, ...]=(), confidence: float=0.6, stability: str='tentative', title: str='购物偏好待确认', candidate_key: str | None=None) -> list[str]:
    """Queue a candidate through ``WanderRuntimeStore``'s existing API.

    The identity chain is required by that API and ensures the candidate is
    tied to a real reflection decision.  This helper is intentionally not a
    fallback writer: a missing runtime store/identity raises instead of
    silently editing the self-book.
    """
    if runtime_store is None:
        raise ValueError('runtime_store_required_for_self_book_candidate')
    statement = _clean(statement, 1000)
    if not statement:
        raise ValueError('shopping_principle_required')
    task_ids = [_clean(item, 120) for item in shopping_task_ids if _clean(item, 120)]
    key = _clean(candidate_key or hashlib.sha256(statement.encode('utf-8')).hexdigest()[:32], 100)
    evidence = {'kind': 'shopping_decision', 'shopping_task_ids': task_ids, 'note': '购物选择的历史兼容候选记录；独立入自我书消费者已停用，不代表待补跑任务'}
    return runtime_store.save_self_book_candidates(run_id=run_id, activity_id=activity_id, node_id=node_id, decision_id=decision_id, candidates=[{'candidate_id': uuid4().hex, 'candidate_key': key, 'title': _clean(title, 200), 'category': _clean(category, 100), 'statement': statement, 'keywords': [_clean(item, 80) for item in keywords if _clean(item, 80)], 'evidence_node_ids': task_ids, 'evidence_summary': _json(evidence), 'confidence': max(0.0, min(1.0, float(confidence))), 'stability': _clean(stability, 40) or 'tentative'}])

def should_queue_preference(*, repeated_principle: bool=False, explicit_persistent: bool=False) -> bool:
    """Return whether a selection may create a pending self-book candidate."""
    return bool(repeated_principle or explicit_persistent)
__all__ = ['ShoppingDecision', 'ShoppingDecisionLedger', 'queue_pending_self_book_candidate', 'should_queue_preference']
