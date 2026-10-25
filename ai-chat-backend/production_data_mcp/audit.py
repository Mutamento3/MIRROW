from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
from typing import Any

from .secure_state import load_or_create_secret


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


class AuditLog:
    """Append-only HMAC chain. Records never contain request or result bodies."""

    def __init__(self, state_dir: Path):
        self._key = load_or_create_secret(state_dir, "audit-hmac", 32)
        self.path = state_dir / "audit.jsonl"
        self._lock = threading.Lock()
        self._sequence = 0
        self._head = "0" * 64
        self._verify_existing()

    def digest(self, value: Any) -> str:
        return hmac.new(self._key, _canonical(value), hashlib.sha256).hexdigest()

    def _verify_existing(self) -> None:
        if not self.path.exists():
            return
        previous = "0" * 64
        sequence = 0
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    record = json.loads(line)
                    signature = record.pop("mac")
                    if record.get("previous") != previous or record.get("sequence") != sequence + 1:
                        raise RuntimeError("audit chain discontinuity")
                    expected = hmac.new(self._key, _canonical(record), hashlib.sha256).hexdigest()
                    if not hmac.compare_digest(signature, expected):
                        raise RuntimeError("audit chain signature mismatch")
                    previous = signature
                    sequence += 1
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("audit log verification failed") from exc
        self._sequence = sequence
        self._head = previous

    def record(
        self,
        *,
        operation: str,
        subject: str,
        parameters: Any,
        status: str,
        duration_ms: int = 0,
        result_count: int | None = None,
        sources: list[str] | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._lock:
            record = {
                "sequence": self._sequence + 1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation": operation,
                "subject": self.digest(subject),
                "parameters": self.digest(parameters),
                "status": status,
                "duration_ms": max(0, int(duration_ms)),
                "result_count": result_count,
                "sources": sorted(set(sources or [])),
                "error_code": error_code,
                "previous": self._head,
            }
            signature = hmac.new(self._key, _canonical(record), hashlib.sha256).hexdigest()
            encoded = json.dumps({**record, "mac": signature}, ensure_ascii=True, separators=(",", ":"))
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(encoded + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise RuntimeError("audit append failed; request denied") from exc
            self._sequence += 1
            self._head = signature
