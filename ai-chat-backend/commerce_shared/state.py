"""Persistent commerce request state machine and submit idempotency boundary."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .contracts import ShoppingProposal, ShoppingRequest, now_iso


class CommerceState(str, Enum):
    REQUESTED = "requested"
    RESEARCHING = "researching"
    PROPOSED = "proposed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    PREPARING = "preparing"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    NEEDS_USER = "needs_user"
    RECONCILE_REQUIRED = "reconcile_required"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidStateTransition(RuntimeError):
    pass


class RequestConflict(RuntimeError):
    """The same request id was reused for a different immutable brief."""


_TRANSITIONS: dict[str, frozenset[str]] = {
    CommerceState.REQUESTED.value: frozenset({CommerceState.RESEARCHING.value, CommerceState.CANCELLED.value}),
    CommerceState.RESEARCHING.value: frozenset({CommerceState.PROPOSED.value, CommerceState.AWAITING_CONFIRMATION.value, CommerceState.NEEDS_USER.value, CommerceState.FAILED.value, CommerceState.CANCELLED.value}),
    CommerceState.PROPOSED.value: frozenset({CommerceState.AWAITING_CONFIRMATION.value, CommerceState.CONFIRMED.value, CommerceState.CANCELLED.value, CommerceState.FAILED.value}),
    CommerceState.AWAITING_CONFIRMATION.value: frozenset({CommerceState.CONFIRMED.value, CommerceState.CANCELLED.value, CommerceState.NEEDS_USER.value}),
    CommerceState.CONFIRMED.value: frozenset({CommerceState.PREPARING.value, CommerceState.PROPOSED.value, CommerceState.AWAITING_CONFIRMATION.value, CommerceState.CANCELLED.value, CommerceState.NEEDS_USER.value}),
    CommerceState.PREPARING.value: frozenset({CommerceState.SUBMITTING.value, CommerceState.NEEDS_USER.value, CommerceState.FAILED.value, CommerceState.CANCELLED.value}),
    CommerceState.SUBMITTING.value: frozenset({CommerceState.SUBMITTED.value, CommerceState.RECONCILE_REQUIRED.value, CommerceState.NEEDS_USER.value, CommerceState.FAILED.value}),
    CommerceState.RECONCILE_REQUIRED.value: frozenset({CommerceState.SUBMITTED.value, CommerceState.FAILED.value, CommerceState.NEEDS_USER.value, CommerceState.CANCELLED.value}),
    CommerceState.SUBMITTED.value: frozenset(),
    CommerceState.NEEDS_USER.value: frozenset({CommerceState.CONFIRMED.value, CommerceState.PROPOSED.value, CommerceState.CANCELLED.value, CommerceState.FAILED.value}),
    CommerceState.FAILED.value: frozenset(),
    CommerceState.CANCELLED.value: frozenset(),
}


def _dump(value: Any) -> str:
    # The request and proposal contracts are already privacy-minimal.  Avoid
    # persisting arbitrary object reprs or full UI pages by using JSON only.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _load(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


@dataclass(frozen=True)
class SubmitClaim:
    request_id: str
    submit_token: str
    state: str
    newly_claimed: bool


class CommerceStateStore:
    """SQLite state store; importing this module never opens a database."""

    def __init__(self, db_path: str | Path = "data/commerce.db") -> None:
        self.db_path = Path(db_path)
        self.initialize()

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
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
            conn.executescript("""
              CREATE TABLE IF NOT EXISTS commerce_requests (
                request_id TEXT PRIMARY KEY,
                merchant TEXT NOT NULL,
                brief TEXT NOT NULL,
                max_payable TEXT,
                fulfillment_mode TEXT NOT NULL,
                address_label TEXT NOT NULL,
                constraints_json TEXT NOT NULL,
                state TEXT NOT NULL,
                proposal_json TEXT NOT NULL DEFAULT '',
                selected_candidate_id TEXT NOT NULL DEFAULT '',
                selected_candidate_hash TEXT NOT NULL DEFAULT '',
                submit_token TEXT NOT NULL DEFAULT '',
                submit_binding_hash TEXT NOT NULL DEFAULT '',
                request_fingerprint TEXT NOT NULL DEFAULT '',
                submit_started_at TEXT,
                submitted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS commerce_state_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL REFERENCES commerce_requests(request_id),
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
              );
              CREATE INDEX IF NOT EXISTS idx_commerce_state_events_request ON commerce_state_events(request_id, event_id);
            """)
            # The state store predates the commerce MCPs in some local data
            # directories.  Add only the narrow integrity columns required by
            # this state machine; never rewrite existing request contents.
            for column, declaration in (
                ("selected_candidate_hash", "TEXT NOT NULL DEFAULT ''"),
                ("submit_binding_hash", "TEXT NOT NULL DEFAULT ''"),
                ("request_fingerprint", "TEXT NOT NULL DEFAULT ''"),
            ):
                try:
                    conn.execute(f"ALTER TABLE commerce_requests ADD COLUMN {column} {declaration}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise

    @staticmethod
    def _request_fingerprint(request: ShoppingRequest | Mapping[str, Any]) -> str:
        if isinstance(request, ShoppingRequest):
            data = request.to_dict()
        else:
            data = dict(request)
        immutable = {
            "merchant": data.get("merchant", ""),
            "brief": data.get("brief", ""),
            "max_payable": data.get("max_payable"),
            "fulfillment_mode": data.get("fulfillment_mode", "delivery"),
            "address_label": data.get("address_label", "default"),
            "constraints": data.get("constraints") or {},
        }
        return hashlib.sha256(_dump(immutable).encode("utf-8", "ignore")).hexdigest()

    @staticmethod
    def _binding_hash(binding: Mapping[str, Any]) -> str:
        return hashlib.sha256(_dump(dict(binding)).encode("utf-8", "ignore")).hexdigest()

    def create_request(self, request: ShoppingRequest) -> dict[str, Any]:
        data = request.to_dict()
        fingerprint = self._request_fingerprint(request)
        now = now_iso()
        with self._connection() as conn:
            existing = conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request.request_id,)).fetchone()
            if existing is not None:
                existing_fingerprint = str(existing["request_fingerprint"] or "") if "request_fingerprint" in existing.keys() else ""
                if not existing_fingerprint:
                    existing_data = {
                        "merchant": existing["merchant"], "brief": existing["brief"],
                        "max_payable": existing["max_payable"], "fulfillment_mode": existing["fulfillment_mode"],
                        "address_label": existing["address_label"], "constraints": _load(existing["constraints_json"], {}),
                    }
                    existing_fingerprint = self._request_fingerprint(existing_data)
                if existing_fingerprint != fingerprint:
                    raise RequestConflict("request_id_reused_with_different_brief")
                return self._decode(existing)
            conn.execute("""
                INSERT INTO commerce_requests(request_id,merchant,brief,max_payable,fulfillment_mode,address_label,constraints_json,state,request_fingerprint,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (request.request_id, request.merchant, request.brief, None if request.max_payable is None else str(request.max_payable), request.fulfillment_mode, request.address_label, _dump(request.constraints), CommerceState.REQUESTED.value, fingerprint, now, now))
            conn.execute("INSERT INTO commerce_state_events(request_id,from_state,to_state,reason,created_at) VALUES(?,?,?,?,?)", (request.request_id, "", CommerceState.REQUESTED.value, "request_created", now))
            row = conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request.request_id,)).fetchone()
        return self._decode(row)

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (str(request_id or ""),)).fetchone()
        return self._decode(row) if row is not None else None

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["constraints"] = _load(data.pop("constraints_json", ""), {})
        proposal = _load(data.pop("proposal_json", ""), None)
        data["proposal"] = proposal
        return data

    def transition(self, request_id: str, to_state: str, *, reason: str = "") -> dict[str, Any]:
        to_state = str(to_state or "").strip().lower()
        if to_state not in _TRANSITIONS:
            raise InvalidStateTransition("unknown_target_state")
        now = now_iso()
        with self._connection() as conn:
            row = conn.execute("SELECT state FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError("request_not_found")
            current = str(row["state"])
            if current == to_state:
                return self._decode(conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone())
            if to_state not in _TRANSITIONS.get(current, frozenset()):
                raise InvalidStateTransition(f"{current}_to_{to_state}_not_allowed")
            conn.execute("UPDATE commerce_requests SET state=?,updated_at=? WHERE request_id=?", (to_state, now, request_id))
            conn.execute("INSERT INTO commerce_state_events(request_id,from_state,to_state,reason,created_at) VALUES(?,?,?,?,?)", (request_id, current, to_state, str(reason or "")[:400], now))
            result = self._decode(conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone())
        return result

    def save_proposal(self, request_id: str, proposal: ShoppingProposal | Mapping[str, Any]) -> dict[str, Any]:
        payload = proposal.to_dict() if isinstance(proposal, ShoppingProposal) else dict(proposal)
        with self._connection() as conn:
            row = conn.execute("SELECT state FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError("request_not_found")
            current = row["state"]
            if current not in {CommerceState.RESEARCHING.value, CommerceState.REQUESTED.value, CommerceState.PROPOSED.value, CommerceState.AWAITING_CONFIRMATION.value}:
                raise InvalidStateTransition("proposal_not_allowed_in_current_state")
            now = now_iso()
            conn.execute("UPDATE commerce_requests SET proposal_json=?,state=?,updated_at=? WHERE request_id=?", (_dump(payload), CommerceState.AWAITING_CONFIRMATION.value, now, request_id))
            conn.execute("INSERT INTO commerce_state_events(request_id,from_state,to_state,reason,created_at) VALUES(?,?,?,?,?)", (request_id, current, CommerceState.AWAITING_CONFIRMATION.value, "proposal_saved", now))
            return self._decode(conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone())

    def select_candidate(self, request_id: str, candidate_id: str) -> dict[str, Any]:
        candidate_id = str(candidate_id or "").strip()
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError("request_not_found")
            if row["state"] not in {CommerceState.AWAITING_CONFIRMATION.value, CommerceState.PROPOSED.value, CommerceState.CONFIRMED.value}:
                raise InvalidStateTransition("candidate_selection_not_allowed")
            proposal = _load(row["proposal_json"], {})
            candidates = proposal.get("candidates", []) if isinstance(proposal, dict) else []
            selected_data = next((c for c in candidates if isinstance(c, dict) and str(c.get("candidate_id")) == candidate_id), None)
            if selected_data is None:
                raise ValueError("candidate_not_found")
            now = now_iso()
            current = row["state"]
            old_candidate = str(row["selected_candidate_id"] or "")
            if current == CommerceState.CONFIRMED.value and old_candidate and old_candidate != candidate_id:
                raise InvalidStateTransition("candidate_immutable_after_confirmation")
            candidate_hash = self._binding_hash(selected_data)
            conn.execute("UPDATE commerce_requests SET selected_candidate_id=?,selected_candidate_hash=?,state=?,updated_at=? WHERE request_id=?", (candidate_id, candidate_hash, CommerceState.CONFIRMED.value, now, request_id))
            if current != CommerceState.CONFIRMED.value:
                conn.execute("INSERT INTO commerce_state_events(request_id,from_state,to_state,reason,created_at) VALUES(?,?,?,?,?)", (request_id, current, CommerceState.CONFIRMED.value, "candidate_confirmed", now))
            return self._decode(conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone())

    def claim_submit(self, request_id: str, submit_token: str, *, binding: Mapping[str, Any] | None = None) -> SubmitClaim:
        """Atomically move CONFIRMED → PREPARING → SUBMITTING once.

        A previously SUBMITTING request always returns ``newly_claimed=False``;
        callers must reconcile externally and must not click submit again.
        """

        request_id, submit_token = str(request_id or ""), str(submit_token or "")
        if not request_id or not submit_token:
            raise ValueError("submit_identity_required")
        if not isinstance(binding, Mapping):
            raise ValueError("submit_binding_required")
        required = ("request_id", "candidate_id", "sku", "basket", "address_label", "price_hash")
        if any(key not in binding for key in required):
            raise ValueError("submit_binding_incomplete")
        binding_data = dict(binding)
        if str(binding_data.get("request_id") or "") != request_id:
            raise ValueError("submit_binding_request_mismatch")
        binding_data["request_id"] = request_id
        binding_hash = self._binding_hash(binding_data)
        now = now_iso()
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError("request_not_found")
            if str(binding_data.get("candidate_id") or "") != str(row["selected_candidate_id"] or ""):
                raise ValueError("submit_binding_candidate_mismatch")
            state = str(row["state"])
            if state == CommerceState.SUBMITTING.value:
                token = str(row["submit_token"] or "") or submit_token
                if token != submit_token or (row["submit_binding_hash"] and row["submit_binding_hash"] != binding_hash):
                    raise InvalidStateTransition("submit_identity_conflict")
                return SubmitClaim(request_id, token, state, False)
            if state == CommerceState.SUBMITTED.value:
                if str(row["submit_token"] or submit_token) != submit_token or (row["submit_binding_hash"] and row["submit_binding_hash"] != binding_hash):
                    raise InvalidStateTransition("submit_identity_conflict")
                return SubmitClaim(request_id, str(row["submit_token"] or submit_token), state, False)
            if state != CommerceState.CONFIRMED.value:
                raise InvalidStateTransition("submit_requires_confirmed_candidate")
            conn.execute("UPDATE commerce_requests SET state=?,updated_at=? WHERE request_id=?", (CommerceState.PREPARING.value, now, request_id))
            conn.execute("INSERT INTO commerce_state_events(request_id,from_state,to_state,reason,created_at) VALUES(?,?,?,?,?)", (request_id, state, CommerceState.PREPARING.value, "submit_prepared", now))
            conn.execute("UPDATE commerce_requests SET state=?,submit_token=?,submit_binding_hash=?,submit_started_at=?,updated_at=? WHERE request_id=?", (CommerceState.SUBMITTING.value, submit_token, binding_hash, now, now, request_id))
            conn.execute("INSERT INTO commerce_state_events(request_id,from_state,to_state,reason,created_at) VALUES(?,?,?,?,?)", (request_id, CommerceState.PREPARING.value, CommerceState.SUBMITTING.value, "submit_claimed", now))
        return SubmitClaim(request_id, submit_token, CommerceState.SUBMITTING.value, True)

    def mark_submitted(self, request_id: str, submit_token: str, *, order_ref: str = "") -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError("request_not_found")
            if row["state"] == CommerceState.SUBMITTED.value:
                if str(row["submit_token"] or "") != str(submit_token):
                    raise InvalidStateTransition("submit_token_mismatch")
                return self._decode(row)
            if row["state"] not in {CommerceState.SUBMITTING.value, CommerceState.RECONCILE_REQUIRED.value} or str(row["submit_token"]) != str(submit_token):
                raise InvalidStateTransition("submit_token_mismatch")
            now = now_iso()
            conn.execute("UPDATE commerce_requests SET state=?,submitted_at=?,updated_at=? WHERE request_id=?", (CommerceState.SUBMITTED.value, now, now, request_id))
            from_state = str(row["state"])
            reason = "order_reconciled" if from_state == CommerceState.RECONCILE_REQUIRED.value else "order_submitted"
            if order_ref:
                reason += ":" + str(order_ref)[:100]
            conn.execute("INSERT INTO commerce_state_events(request_id,from_state,to_state,reason,created_at) VALUES(?,?,?,?,?)", (request_id, from_state, CommerceState.SUBMITTED.value, reason, now))
            return self._decode(conn.execute("SELECT * FROM commerce_requests WHERE request_id=?", (request_id,)).fetchone())

    def mark_reconcile_required(self, request_id: str, *, reason: str = "submit_interrupted") -> dict[str, Any]:
        return self.transition(request_id, CommerceState.RECONCILE_REQUIRED.value, reason=reason)

    def reconcile_result(self, request_id: str, *, found: bool, order_ref: str = "", reason: str = "") -> dict[str, Any]:
        row = self.get_request(request_id)
        if row is None:
            raise KeyError("request_not_found")
        if row["state"] != CommerceState.RECONCILE_REQUIRED.value:
            if found and row["state"] == CommerceState.SUBMITTED.value:
                return row
            raise InvalidStateTransition("reconcile_requires_reconcile_state")
        if found:
            return self.mark_submitted(request_id, row.get("submit_token", ""), order_ref=order_ref)
        # No order was found: the request must go to needs_user.  It is never
        # automatically re-claimed; a new explicit confirmation is required.
        return self.transition(request_id, CommerceState.NEEDS_USER.value, reason=reason or "order_not_found_after_interruption")

    def events(self, request_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT request_id,from_state,to_state,reason,created_at FROM commerce_state_events WHERE request_id=? ORDER BY event_id", (request_id,)).fetchall()
        return [dict(row) for row in rows]


__all__ = ["CommerceState", "CommerceStateStore", "InvalidStateTransition", "RequestConflict", "SubmitClaim"]
