"""Deterministic, evidence-first candidate analysis shared by domain MCPs.

The real app adapters may provide different labels on different screens.  The
normalizers here accept a deliberately small set of aliases and preserve the
source/time semantics instead of pretending preview and detail sales agree.
They produce compact cards; raw UI/XML/review material remains in the domain
adapter.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from .contracts import CandidateKind, Evidence, ShoppingCandidate, now_iso, stable_candidate_id
from .limits import money_cents

def _text(value: Any, limit: int=500) -> str:
    return ' '.join(str(value or '').split())[:limit]

def _first(raw: Mapping[str, Any], *keys: str, default: Any='') -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ''):
            return raw[key]
    return default

def _number(value: Any, default: float=0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    try:
        text = str(value).replace(',', '').replace('¥', '').replace('￥', '').strip()
        return float(text)
    except (TypeError, ValueError):
        return default

def _bool(raw: Mapping[str, Any], *keys: str) -> bool:
    value = _first(raw, *keys, default=False)
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y', '是', '有'}
    return bool(value)

def _captured(raw: Mapping[str, Any], *keys: str) -> str:
    value = _first(raw, *keys, default='')
    return _text(value, 60) or now_iso()

def _evidence(source: str, field: str, value: Any, *, captured_at: str='', confidence: str='observed', note: str='') -> Evidence:
    return Evidence(source=_text(source, 80), field=_text(field, 80), value=_text(value, 500), captured_at=_text(captured_at, 60) or now_iso(), confidence=_text(confidence, 30), note=_text(note, 300))

@dataclass(frozen=True)
class CandidateAssessment:
    match_score: float
    trust_score: float
    value_score: float
    evidence: tuple[Evidence, ...]
    risk_signals: tuple[str, ...]

def _candidate_price(raw: Mapping[str, Any]) -> float:
    value = _first(raw, 'final_payable', 'payable', 'after_coupon_price', 'price', 'total', default=None)
    if value is None:
        raise ValueError('candidate_price_required')
    cents = money_cents(value)
    return cents / 100

def _to_candidate(raw: Mapping[str, Any], *, merchant: str, kind: str, assessment: CandidateAssessment | None=None) -> ShoppingCandidate:
    title = _text(_first(raw, 'title', 'item_title', 'name', 'dish_name', default='未命名候选'), 300)
    price = _candidate_price(raw)
    candidate_id = _text(_first(raw, 'candidate_id', 'id', 'item_id', default=''), 160) or stable_candidate_id(merchant, title, price, _first(raw, 'url', 'item_url', default=''))
    assessment = assessment or CandidateAssessment(0, 0, 0, (), ())
    reason = _text(_first(raw, 'reason', default=''), 700)
    if not reason:
        reason = {CandidateKind.BEST_MATCH.value: '款式/需求匹配度优先，结合可见评价证据', CandidateKind.MOST_TRUSTWORTHY.value: '店铺与售后证据相对稳妥，风险信号较少', CandidateKind.BEST_VALUE.value: '优惠后实付与同款相对价格更有优势'}.get(kind, '基于可见证据的候选')
    evidence = list(assessment.evidence)
    if not evidence:
        evidence.append(_evidence(f'{merchant}_adapter', 'candidate_observed', '候选来自域 MCP 页面读取', confidence='observed'))
    metadata = {'source_semantics': _first(raw, 'source_semantics', 'sales_semantics', default=''), 'captured_at': _first(raw, 'captured_at', 'detail_captured_at', 'observed_at', default=now_iso()), 'score': {'match': assessment.match_score, 'trust': assessment.trust_score, 'value': assessment.value_score}}
    if isinstance(raw.get('coupon_breakdown'), Mapping):
        metadata['coupon_breakdown'] = dict(raw['coupon_breakdown'])
    if isinstance(raw.get('optional_add_on'), Mapping):
        metadata['optional_add_on'] = dict(raw['optional_add_on'])
    return ShoppingCandidate(candidate_id=candidate_id, title=title, final_payable=price, kind=kind, reason=reason, evidence=tuple(evidence), risk_signals=assessment.risk_signals, store_name=_text(_first(raw, 'store_name', 'shop_name', default=''), 120), store_address=_text(_first(raw, 'store_address', 'branch_address', default=''), 200), url=_text(_first(raw, 'url', 'item_url', 'deep_link', default=''), 500), quantity=1, item_lines=tuple((item for item in _first(raw, 'item_lines', default=[]) or [] if isinstance(item, Mapping))), metadata=metadata)

def build_candidate_options(raw_candidates: Iterable[Mapping[str, Any] | ShoppingCandidate], *, merchant: str) -> tuple[tuple[ShoppingCandidate, ...], tuple[str, ...]]:
    """Return up to three distinct option categories and truthful limits."""
    normalized: list[tuple[ShoppingCandidate, CandidateAssessment]] = []
    seen: set[str] = set()
    for raw in raw_candidates:
        if isinstance(raw, ShoppingCandidate):
            candidate = raw
            assessment = CandidateAssessment(0, 0, 0, candidate.evidence, candidate.risk_signals)
        elif isinstance(raw, Mapping):
            assessment = CandidateAssessment(_number(_first(raw, 'match_score', 'style_score', default=0)), _number(_first(raw, 'trust_score', 'store_score', default=0)), _number(_first(raw, 'value_score', default=0)), (), ())
            candidate = _to_candidate(raw, merchant=merchant, kind=CandidateKind.BEST_MATCH.value, assessment=assessment)
        else:
            continue
        if candidate.candidate_id in seen:
            continue
        seen.add(candidate.candidate_id)
        normalized.append((candidate, assessment))
    if not normalized:
        return ((), ('未获得可用候选或页面证据',))
    selectors = ((CandidateKind.BEST_MATCH.value, lambda item: (item[1].match_score, -float(item[0].final_payable))), (CandidateKind.MOST_TRUSTWORTHY.value, lambda item: (item[1].trust_score - len(item[1].risk_signals) * 8, item[1].match_score)), (CandidateKind.BEST_VALUE.value, lambda item: (item[1].value_score, -float(item[0].final_payable))))
    chosen: list[ShoppingCandidate] = []
    used: set[str] = set()
    limitations: list[str] = []
    for kind, key in selectors:
        ordered = sorted(normalized, key=key, reverse=True)
        pick = next((item for item in ordered if item[0].candidate_id not in used), None)
        if pick is None:
            pick = None
        if pick is None:
            limitations.append(f'缺少{kind}候选')
            continue
        candidate, assessment = pick
        data = candidate.to_dict()
        category_reason = {CandidateKind.BEST_MATCH.value: '款式/需求匹配度优先，结合可见评价证据', CandidateKind.MOST_TRUSTWORTHY.value: '店铺与售后证据相对稳妥，风险信号较少', CandidateKind.BEST_VALUE.value: '优惠后实付与同款相对价格更有优势'}.get(kind, candidate.reason)
        generic_reasons = {'款式/需求匹配度优先，结合可见评价证据', '店铺与售后证据相对稳妥，风险信号较少', '优惠后实付与同款相对价格更有优势'}
        data['reason'] = category_reason if not candidate.reason or candidate.reason in generic_reasons else f'{category_reason}；{candidate.reason}'
        chosen.append(_to_candidate(data, merchant=merchant, kind=kind, assessment=assessment))
        used.add(candidate.candidate_id)
    if len(normalized) < 3:
        limitations.append(f'仅获得 {len(normalized)} 个独立候选，未凑足三类')
    return (tuple(chosen), tuple(limitations))

@dataclass(frozen=True)
class CouponPlan:
    base_total: float
    store_coupon: float
    platform_coupon: float
    delivery_fee: float
    packing_fee: float
    final_total: float
    optional_add_on_total: float | None = None
    optional_savings: float = 0.0
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {'base_total': self.base_total, 'store_coupon': self.store_coupon, 'platform_coupon': self.platform_coupon, 'delivery_fee': self.delivery_fee, 'packing_fee': self.packing_fee, 'final_total': self.final_total, 'optional_add_on_total': self.optional_add_on_total, 'optional_savings': self.optional_savings, 'notes': list(self.notes)}

def plan_meituan_coupons(raw: Mapping[str, Any], *, include_optional_add_on: bool=False) -> CouponPlan:
    """Apply only checkout-visible store coupons and already-held platform coupons."""
    base = _number(_first(raw, 'base_total', 'basket_total', 'subtotal', default=0))
    store = _number(_first(raw, 'store_coupon', 'store_discount', default=0))
    platform = _number(_first(raw, 'platform_coupon', 'platform_discount', 'held_platform_coupon', default=0))
    delivery = _number(_first(raw, 'delivery_fee', 'delivery_charge', default=0))
    packing = _number(_first(raw, 'packing_fee', 'packaging_fee', default=0))
    final = max(0.0, round(base - store - platform + delivery + packing, 2))
    notes: list[str] = []
    if _bool(raw, 'store_coupon_claimable', 'can_claim_store_coupon') and (not store):
        notes.append('下单页可领取店铺券，需在结算页领取并应用')
    if _bool(raw, 'platform_coupon_available', 'has_platform_coupon') and (not platform):
        notes.append('已有平台券可添加；本轮不进入领券页')
    optional_total = None
    optional_savings = 0.0
    optional = raw.get('optional_add_on')
    if include_optional_add_on and isinstance(optional, Mapping):
        optional_base = _number(_first(optional, 'base_total', 'basket_total', 'subtotal', default=base))
        optional_store = _number(_first(optional, 'store_coupon', 'store_discount', default=store))
        optional_platform = _number(_first(optional, 'platform_coupon', 'platform_discount', default=platform))
        optional_delivery = _number(_first(optional, 'delivery_fee', default=delivery))
        optional_packing = _number(_first(optional, 'packing_fee', default=packing))
        optional_total = max(0.0, round(optional_base - optional_store - optional_platform + optional_delivery + optional_packing, 2))
        optional_savings = round(final - optional_total, 2)
        if optional_savings <= 0:
            optional_total, optional_savings = (None, 0.0)
        else:
            notes.append('加入未请求商品后实付更低，仅作为待 AI 同意的可选方案')
    return CouponPlan(base, store, platform, delivery, packing, final, optional_total, optional_savings, tuple(notes))
__all__ = ['CandidateAssessment', 'CouponPlan', 'build_candidate_options', 'plan_meituan_coupons']
