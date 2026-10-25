"""JSON-safe, privacy-minimal contracts shared by commerce MCPs."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping
from uuid import uuid4

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class FulfillmentMode(str, Enum):
    DELIVERY = 'delivery'
    VOUCHER = 'voucher'

class CandidateKind(str, Enum):
    BEST_MATCH = 'best_match'
    MOST_TRUSTWORTHY = 'most_trustworthy'
    BEST_VALUE = 'best_value_after_coupons'
_SENSITIVE_KEYS = ('password', 'token', 'secret', 'cookie', 'authorization', 'phone', 'mobile', 'address', 'name')

def _clean_text(value: Any, limit: int=500) -> str:
    value = re.sub('\\s+', ' ', str(value or '')).strip()
    return value[:limit]

def _safe_json(value: Any) -> Any:
    """Strip fields that must never cross the AI-facing proposal contract."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if any((part in key_text.lower() for part in _SENSITIVE_KEYS)):
                continue
            result[key_text] = _safe_json(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)

@dataclass(frozen=True)
class Evidence:
    """A compact fact card; raw page/UI material remains inside a domain MCP."""
    source: str
    field: str
    value: str
    captured_at: str = field(default_factory=now_iso)
    confidence: str = 'unknown'
    note: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {'source': _clean_text(self.source, 80), 'field': _clean_text(self.field, 80), 'value': _clean_text(self.value, 500), 'captured_at': _clean_text(self.captured_at, 60), 'confidence': _clean_text(self.confidence, 30), 'note': _clean_text(self.note, 300)}

@dataclass(frozen=True)
class ShoppingRequest:
    merchant: str
    brief: str
    max_payable: int | float | str | None
    request_id: str = field(default_factory=lambda: uuid4().hex)
    fulfillment_mode: str = FulfillmentMode.DELIVERY.value
    address_label: str = 'default'
    requested_at: str = field(default_factory=now_iso)
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        merchant = _clean_text(self.merchant, 30).lower()
        if merchant != 'meituan':
            raise ValueError('merchant_must_be_meituan')
        if not _clean_text(self.brief, 1000):
            raise ValueError('shopping_brief_required')
        if self.max_payable is not None:
            try:
                if float(self.max_payable) <= 0:
                    raise ValueError('max_payable_must_be_positive')
            except (TypeError, ValueError) as exc:
                if isinstance(exc, ValueError) and str(exc) == 'max_payable_must_be_positive':
                    raise
                raise ValueError('max_payable_invalid') from exc
        if self.fulfillment_mode not in {m.value for m in FulfillmentMode}:
            raise ValueError('unsupported_fulfillment_mode')
        object.__setattr__(self, 'merchant', merchant)
        object.__setattr__(self, 'brief', _clean_text(self.brief, 1000))
        object.__setattr__(self, 'address_label', _clean_text(self.address_label, 30) or 'default')
        object.__setattr__(self, 'constraints', _safe_json(dict(self.constraints or {})))

    def to_dict(self) -> dict[str, Any]:
        return {'request_id': self.request_id, 'merchant': self.merchant, 'brief': self.brief, 'max_payable': self.max_payable, 'fulfillment_mode': self.fulfillment_mode, 'address_label': self.address_label, 'requested_at': self.requested_at, 'constraints': dict(self.constraints)}

@dataclass(frozen=True)
class ShoppingCandidate:
    candidate_id: str
    title: str
    final_payable: int | float | str
    kind: str = CandidateKind.BEST_MATCH.value
    reason: str = ''
    evidence: tuple[Evidence, ...] = ()
    risk_signals: tuple[str, ...] = ()
    store_name: str = ''
    store_address: str = ''
    url: str = ''
    quantity: int = 1
    item_lines: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            if float(self.final_payable) < 0:
                raise ValueError('candidate_price_invalid')
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc) == 'candidate_price_invalid':
                raise
            raise ValueError('candidate_price_invalid') from exc
        if int(self.quantity) != 1:
            raise ValueError('quantity_must_be_one')
        object.__setattr__(self, 'title', _clean_text(self.title, 300))
        object.__setattr__(self, 'reason', _clean_text(self.reason, 700))
        object.__setattr__(self, 'risk_signals', tuple((_clean_text(item, 180) for item in self.risk_signals if _clean_text(item, 180))))
        object.__setattr__(self, 'item_lines', tuple((_safe_json(dict(item)) for item in self.item_lines)))
        object.__setattr__(self, 'metadata', _safe_json(dict(self.metadata or {})))

    def to_dict(self) -> dict[str, Any]:
        return {'candidate_id': self.candidate_id, 'title': self.title, 'final_payable': self.final_payable, 'kind': self.kind, 'reason': self.reason, 'evidence': [item.to_dict() if isinstance(item, Evidence) else dict(item) for item in self.evidence], 'risk_signals': list(self.risk_signals), 'store_name': _clean_text(self.store_name, 120), 'store_address': _clean_text(self.store_address, 200), 'url': _clean_text(self.url, 500), 'quantity': 1, 'item_lines': list(self.item_lines), 'metadata': dict(self.metadata)}

@dataclass(frozen=True)
class ShoppingProposal:
    request_id: str
    merchant: str
    candidates: tuple[ShoppingCandidate, ...]
    status: str = 'awaiting_confirmation'
    generated_at: str = field(default_factory=now_iso)
    limitations: tuple[str, ...] = ()
    evidence_scope: str = 'domain_mcp_compact_evidence'

    def to_dict(self) -> dict[str, Any]:
        return {'request_id': self.request_id, 'merchant': self.merchant, 'status': self.status, 'generated_at': self.generated_at, 'candidates': [candidate.to_dict() for candidate in self.candidates], 'limitations': list(self.limitations), 'evidence_scope': self.evidence_scope}

def stable_candidate_id(*parts: Any) -> str:
    raw = '|'.join((_clean_text(item, 500) for item in parts))
    return hashlib.sha256(raw.encode('utf-8', 'ignore')).hexdigest()[:32]

def as_json(value: Any) -> str:
    return json.dumps(_safe_json(value), ensure_ascii=False, sort_keys=True, default=str)
__all__ = ['CandidateKind', 'Evidence', 'FulfillmentMode', 'ShoppingCandidate', 'ShoppingProposal', 'ShoppingRequest', 'as_json', 'now_iso', 'stable_candidate_id']
