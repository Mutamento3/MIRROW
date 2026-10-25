"""Flash 共振评价输出的严格解析与证据边界校验。"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable


class AffectValidationError(ValueError):
    """Flash 输出不符合 affect 合同。"""


OPERATIONS = {"no_change", "confirm", "replace", "clear"}
EXPLICITNESS = {"explicit", "inferred"}
EXPLICITNESS_ALIASES = {
    "explicit": "explicit",
    "inferred": "inferred",
    "明确": "explicit",
    "自述": "explicit",
    "direct": "explicit",
    "stated": "explicit",
    "self_report": "explicit",
    "self-report": "explicit",
    "explicit_statement": "explicit",
    "explicit_user_statement": "explicit",
    "direct_statement": "explicit",
    "显式": "explicit",
    "明示": "explicit",
    "明确表达": "explicit",
    "直接表达": "explicit",
    "用户自述": "explicit",
    "人类伙伴自述": "explicit",
    "推断": "inferred",
    "识别": "inferred",
    "inferred": "inferred",
    "implicit": "inferred",
    "observed": "inferred",
    "inference": "inferred",
    "semantic_inference": "inferred",
    "context_inferred": "inferred",
    "contextual_inference": "inferred",
    "隐式": "inferred",
    "语义推断": "inferred",
    "上下文推断": "inferred",
    "推测": "inferred",
    # mixed is deliberately conservative: it must not be promoted to a
    # direct/self-report reading just because one part of the batch is clear.
    "mixed": "inferred",
}
TIME_SCOPES = {"current", "recent_past", "past"}
TIME_SCOPE_ALIASES = {
    "current": "current",
    "present": "current",
    "now": "current",
    "ongoing": "current",
    "immediate": "current",
    "right_now": "current",
    "at_present": "current",
    "present_moment": "current",
    "current_turn": "current",
    "this_turn": "current",
    "current_context": "current",
    "当前": "current",
    "现在": "current",
    "此刻": "current",
    "当下": "current",
    "即时": "current",
    "本轮": "current",
    "当前对话": "current",
    "recent_past": "recent_past",
    "recent-past": "recent_past",
    "recent past": "recent_past",
    "recent": "recent_past",
    "recently": "recent_past",
    "just_now": "recent_past",
    "recent_context": "recent_past",
    "recent_conversation": "recent_past",
    "earlier_in_conversation": "recent_past",
    "刚才": "recent_past",
    "近期": "recent_past",
    "最近": "recent_past",
    "刚刚": "recent_past",
    "这段对话": "recent_past",
    "这轮对话": "recent_past",
    "past": "past",
    "historical": "past",
    "history": "past",
    "earlier": "past",
    "previous": "past",
    "historical_context": "past",
    "distant_past": "past",
    "过去": "past",
    "此前": "past",
    "之前": "past",
    "历史": "past",
    "以前": "past",
}

# v2 is intentionally a different contract from the old single resonance
# object.  Keep the v1 parser above for migration diagnostics only; production
# settlement uses this strict multi-component parser.
V2_OPERATIONS = {"add", "reinforce", "reappraise", "resolve", "no_change"}
OWNERS = {"k_self", "human_resonance"}
FAMILIES = {"愉悦", "亲近", "安定", "兴趣", "低落", "担忧", "恼怒", "排斥", "惊讶", "复杂/未归类"}
PERSISTENCE = {"momentary", "situational", "lingering"}
V2_TIME_SCOPES = {"current", "recent_past", "past"}
V2_OPERATION_ALIASES = {
    "add": "add", "create": "add", "new": "add",
    "reinforce": "reinforce", "strengthen": "reinforce", "confirm": "reinforce",
    "reappraise": "reappraise", "re_appraise": "reappraise", "revise": "reappraise",
    "reinterpret": "reappraise", "replace": "reappraise",
    "resolve": "resolve", "clear": "resolve", "close": "resolve",
    "no_change": "no_change", "nochange": "no_change", "unchanged": "no_change",
    "none": "no_change", "noop": "no_change", "no_op": "no_change",
}
V2_OWNER_ALIASES = {
    "human_resonance": "human_resonance",
    "humanresonance": "human_resonance",
    "user_resonance": "human_resonance",
    "resonance": "human_resonance",
    "人类伙伴共振": "human_resonance",
    "k_self": "k_self",
    "kself": "k_self",
}
PERSISTENCE_ALIASES = {
    "momentary": "momentary",
    "moment": "momentary",
    "immediate": "momentary",
    "brief": "momentary",
    "fleeting": "momentary",
    "transient": "momentary",
    "short_term": "momentary",
    "短暂": "momentary",
    "即时": "momentary",
    "situational": "situational",
    "situation": "situational",
    "contextual": "situational",
    "context_dependent": "situational",
    "episode": "situational",
    "temporary": "situational",
    "medium_term": "situational",
    "情境性": "situational",
    "阶段性": "situational",
    "lingering": "lingering",
    "persistent": "lingering",
    "enduring": "lingering",
    "ongoing": "lingering",
    "residual": "lingering",
    "carryover": "lingering",
    "long_term": "lingering",
    "持续": "lingering",
    "余韵": "lingering",
}
TARGET_TYPE_ALIASES = {
    "person": "person", "user": "person", "human": "person", "self": "person",
    "event": "event", "situation": "event", "issue": "event", "occurrence": "event",
    "activity": "activity", "task": "activity", "action": "activity", "work": "activity",
    "unknown": "unknown", "none": "unknown", "null": "unknown", "unspecified": "unknown",
}
V2_EVIDENCE_RELATIONS = {"causal", "corroborating", "self_report"}
V2_EVIDENCE_RELATION_ALIASES = {
    "causal": "causal",
    "cause": "causal",
    "caused_by": "causal",
    "primary": "causal",
    "primary_evidence": "causal",
    "direct_evidence": "causal",
    "corroborating": "corroborating",
    "corroborative": "corroborating",
    "corroboration": "corroborating",
    "support": "corroborating",
    "supporting": "corroborating",
    "supporting_evidence": "corroborating",
    "context": "corroborating",
    "contextual": "corroborating",
    "indirect": "corroborating",
    "self_report": "self_report",
    "self_reported": "self_report",
    "selfreport": "self_report",
    "explicit": "self_report",
    "explicit_statement": "self_report",
    "direct": "self_report",
    "direct_report": "self_report",
    "direct_statement": "self_report",
    "user_statement": "self_report",
}


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        raise AffectValidationError("Flash output must be a JSON object")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AffectValidationError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise AffectValidationError("Flash output must be a JSON object")
    return data


def _bounded_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AffectValidationError(f"{name} must be a number") from exc
    if result < 0 or result > 1:
        raise AffectValidationError(f"{name} must be between 0 and 1")
    return round(result, 4)


def parse_flash_output(raw: Any, *, allowed_source_ids: Iterable[str] = ()) -> dict[str, Any]:
    """解析并校验共振合同。

    Flash 返回的 source IDs 必须来自后端传入的候选集合；模型不能创建
    或引用不存在的事实。返回的 dict 已被规范化，可直接落库。
    """
    data = _json_object(raw)
    operation = str(data.get("operation", "")).strip()
    if operation not in OPERATIONS:
        raise AffectValidationError("operation must be no_change/confirm/replace/clear")
    explicitness = str(data.get("explicitness", "")).strip()
    if explicitness not in EXPLICITNESS:
        raise AffectValidationError("explicitness must be explicit or inferred")
    time_scope = str(data.get("time_scope", "current")).strip() or "current"
    if time_scope not in TIME_SCOPES:
        raise AffectValidationError("invalid time_scope")
    confidence = _bounded_float(data.get("confidence", 0), "confidence")

    observed = data.get("observed_state")
    if observed is None:
        observed = {}
    if not isinstance(observed, dict):
        raise AffectValidationError("observed_state must be an object")
    labels = observed.get("labels", [])
    if isinstance(labels, str):
        labels = [labels]
    if not isinstance(labels, list) or any(not isinstance(item, str) for item in labels):
        raise AffectValidationError("observed_state.labels must be a string array")
    normalized_observed = {
        "labels": [item.strip()[:80] for item in labels if item.strip()][:12],
        "description": str(observed.get("description", ""))[:500],
        "intensity": _bounded_float(observed.get("intensity", 0), "observed_state.intensity") if "intensity" in observed else 0.0,
    }

    evidence = data.get("evidence", [])
    if not isinstance(evidence, list):
        raise AffectValidationError("evidence must be an array")
    allowed = {str(item) for item in allowed_source_ids if str(item)}
    normalized_evidence: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict):
            raise AffectValidationError("each evidence entry must be an object")
        source_id = str(item.get("source_id", "")).strip()
        if not source_id or (allowed and source_id not in allowed):
            raise AffectValidationError(f"evidence source_id is not allowed: {source_id or '<empty>'}")
        normalized_evidence.append({
            "source_id": source_id,
            "excerpt": str(item.get("excerpt", ""))[:240],
        })

    if operation in {"confirm", "replace", "clear"} and not normalized_evidence:
        raise AffectValidationError("state-changing operation requires evidence")
    if operation in {"confirm", "replace"} and not normalized_observed["labels"]:
        raise AffectValidationError("state-changing operation requires labels")

    return {
        "operation": operation,
        "observed_state": normalized_observed,
        "explicitness": explicitness,
        "confidence": confidence,
        "time_scope": time_scope,
        "evidence": normalized_evidence,
        "reason": str(data.get("reason", ""))[:500],
    }


def _v2_number(value: Any, name: str, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AffectValidationError(f"{name} must be a number") from exc
    if result != result or result < low or result > high:
        raise AffectValidationError(f"{name} out of range")
    return round(result, 4)


def _normalise_v2_explicitness(value: Any) -> str:
    raw = str(value or "").strip()
    key = re.sub(r"[\s\-/]+", "_", raw.lower()).strip("_")
    normalized = EXPLICITNESS_ALIASES.get(key) or EXPLICITNESS_ALIASES.get(raw)
    # This is descriptive metadata, not source authority.  An unfamiliar
    # spelling must never be promoted to a direct statement.
    return normalized if normalized in EXPLICITNESS else "inferred"


def _normalise_v2_time_scope(value: Any) -> str:
    raw = str(value or "current").strip()
    key = re.sub(r"[\s\-/]+", "_", raw.lower()).strip("_")
    normalized = TIME_SCOPE_ALIASES.get(key) or TIME_SCOPE_ALIASES.get(raw)
    return normalized if normalized in V2_TIME_SCOPES else "current"


def _v2_enum_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", str(value or "").strip().lower()).strip("_")


def _normalise_v2_operation(value: Any) -> str:
    normalized = V2_OPERATION_ALIASES.get(_v2_enum_key(value))
    if normalized not in V2_OPERATIONS:
        raise AffectValidationError("operation must be add/reinforce/reappraise/resolve/no_change")
    return normalized


def _normalise_v2_owner(value: Any) -> str:
    normalized = V2_OWNER_ALIASES.get(_v2_enum_key(value or "human_resonance"))
    if normalized not in OWNERS:
        raise AffectValidationError("invalid owner")
    return normalized


def _normalise_v2_persistence(value: Any) -> str:
    # Persistence selects a decay recipe.  Unknown provider prose therefore
    # falls back to the neutral middle class instead of aborting a source-
    # bounded appraisal or guessing an unusually short/long lifetime.
    normalized = PERSISTENCE_ALIASES.get(_v2_enum_key(value or "situational"))
    return normalized if normalized in PERSISTENCE else "situational"


def _normalise_v2_target_type(value: Any) -> str:
    normalized = TARGET_TYPE_ALIASES.get(_v2_enum_key(value or "unknown"))
    return normalized if normalized in {"person", "event", "activity", "unknown"} else "unknown"


def _v2_source_revision(value: Any) -> int:
    """Normalise Flash's JSON spelling without weakening source identity.

    Small models sometimes echo the prompt's ``@r1`` marker as ``"r1"`` or
    serialise the same integer as ``"1.0"``.  Revision is transport metadata,
    not model-authored evidence: accept only integer-valued spellings and let
    the exact backend allow-list check below remain authoritative.
    """
    if isinstance(value, bool):
        raise AffectValidationError("evidence source_revision must be an integer")
    raw = str(value if value is not None else 1).strip().lower()
    if raw.startswith("r"):
        raw = raw[1:].strip()
    try:
        numeric = float(raw)
    except (TypeError, ValueError) as exc:
        raise AffectValidationError("evidence source_revision must be an integer") from exc
    if not numeric.is_integer() or numeric < 1:
        raise AffectValidationError("evidence source_revision must be an integer")
    return int(numeric)


def _normalise_v2_evidence_relation(value: Any) -> str:
    """Keep evidence provenance strict while tolerating provider enum prose.

    ``relation`` is a backend projection hint, not a source identity.  Flash
    models sometimes return a semantically equivalent phrase even when the
    prompt lists the enum.  Known aliases preserve their mehuman; an unknown
    spelling is conservatively treated as a causal root so a real, already
    allow-listed source cannot strand the whole quiet settlement merely over
    provider wording.  Exact ``corroborating`` still stays non-root evidence.
    """
    raw = str(value or "causal").strip()
    key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    normalized = V2_EVIDENCE_RELATION_ALIASES.get(key)
    if normalized in V2_EVIDENCE_RELATIONS:
        return normalized
    return "causal"


def _normalise_v2_source_id(value: Any, allowed_ids: set[str]) -> str:
    """Recover only surface-equivalent spellings of an allowed source id."""
    raw = str(value or "").strip().strip('"\'')
    if raw in allowed_ids:
        return raw
    candidates = {raw}
    candidates.add(re.sub(r"(?i)\s*@\s*r\s*\d+(?:\.0+)?\s*$", "", raw).strip())
    candidates.add(re.sub(r"(?i)^\s*source_id\s*[:=]\s*", "", raw).strip().strip('"\''))
    matches = candidates & allowed_ids
    if len(matches) == 1:
        return next(iter(matches))
    raise AffectValidationError("evidence source_id is not allowed")


def _v2_source_ref(item: Any, allowed: set[tuple[str, str, int]], allowed_ids: set[str]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise AffectValidationError("evidence must be an object")
    if not allowed_ids:
        raise AffectValidationError("evidence requires a backend-provided source boundary")
    source_id = _normalise_v2_source_id(item.get("source_id"), allowed_ids)
    source_type = str(item.get("source_type") or "conversation_message").strip()
    try:
        revision = _v2_source_revision(item.get("source_revision", 1) or 1)
    except AffectValidationError:
        # Revision is backend-owned metadata.  When this source identity has
        # exactly one permitted revision, recover that integer instead of
        # failing an otherwise source-bounded appraisal on provider spelling.
        candidates = {
            allowed_revision
            for allowed_type, allowed_id, allowed_revision in allowed
            if allowed_id == source_id
        }
        if len(candidates) != 1:
            raise
        revision = next(iter(candidates))
    if allowed and (source_type, source_id, revision) not in allowed and ("", source_id, revision) not in allowed:
        # Source type is also backend-owned transport metadata.  Accept a
        # provider spelling only when the allowed boundary proves exactly one
        # type for this already matched id+revision.
        allowed_types = {
            allowed_type
            for allowed_type, allowed_id, allowed_revision in allowed
            if allowed_id == source_id and allowed_revision == revision
        }
        if len(allowed_types) != 1:
            raise AffectValidationError("evidence source revision is not allowed")
        source_type = next(iter(allowed_types))
    relation = _normalise_v2_evidence_relation(item.get("relation"))
    return {
        "source_type": source_type,
        "source_id": source_id,
        "source_revision": revision,
        "relation": relation,
        "excerpt": str(item.get("excerpt", ""))[:240],
    }


def parse_flash_output_v2(
    raw: Any,
    *,
    allowed_source_refs: Iterable[dict[str, Any]] = (),
    allowed_source_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Strict multi-component Flash contract used at quiet settlement.

    The provider may describe several co-existing components, but it cannot
    create a source identity or component id.  ``match_component_id`` is
    accepted only as an opaque handle supplied in the prompt; the service
    still verifies it against its current owner projection before applying a
    transition.
    """
    data = _json_object(raw)
    operation = _normalise_v2_operation(data.get("operation"))
    explicitness = _normalise_v2_explicitness(data.get("explicitness") or "inferred")
    time_scope = _normalise_v2_time_scope(data.get("time_scope") or "current")
    confidence = _v2_number(data.get("confidence", 0), "confidence", 0, 1)
    owner = _normalise_v2_owner(data.get("owner"))
    components = data.get("components", [])
    if components is None:
        components = []
    if not isinstance(components, list) or len(components) > 8:
        raise AffectValidationError("components must be an array of at most 8")
    refs = list(allowed_source_refs or [])
    allowed_pairs = {
        (
            str(item.get("source_type") or "conversation_message"),
            str(item.get("source_id")),
            _v2_source_revision(item.get("source_revision", 1) or 1),
        )
        for item in refs if isinstance(item, dict) and item.get("source_id")
    }
    allowed_id_set = {str(item) for item in allowed_source_ids if str(item)} | {
        str(item.get("source_id")) for item in refs if isinstance(item, dict) and item.get("source_id")
    }
    normalized: list[dict[str, Any]] = []
    for item in components:
        if not isinstance(item, dict):
            raise AffectValidationError("component must be an object")
        match_id = str(item.get("match_component_id") or "").strip()
        family = str(item.get("family") or "复杂/未归类").strip()
        if family not in FAMILIES:
            # Families are a loose index, but typoed values should not become
            # an unbounded taxonomy in the persisted projection.
            family = "复杂/未归类"
        label = str(item.get("label") or "").strip()[:120]
        if operation in {"add", "reinforce", "reappraise"} and not label and not match_id:
            raise AffectValidationError("component label is required")
        target = item.get("target") or {}
        if isinstance(target, str):
            target_key = re.sub(r"[\s_\-/]+", "", target.strip().lower())
            if target_key in {"人类伙伴", "用户", "user", "currentuser", "self", "me"}:
                target = {"type": "person", "id": "人类伙伴"}
            elif target_key in {"", "unknown", "none", "null", "未知", "无"}:
                target = {}
            else:
                target = {"type": "unknown", "id": target.strip()[:160]}
        if not isinstance(target, dict):
            target = {}
        target_type = _normalise_v2_target_type(target.get("type"))
        dimensions = item.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            dimensions = {}
        appraisal = item.get("appraisal") or {}
        if not isinstance(appraisal, dict):
            appraisal = {}
        evidence = item.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) > 12:
            raise AffectValidationError("evidence must be an array")
        normalized_evidence = [_v2_source_ref(entry, allowed_pairs, allowed_id_set) for entry in evidence]
        if operation in {"add", "reinforce", "reappraise", "resolve"} and not normalized_evidence:
            raise AffectValidationError("state-changing component requires evidence")
        persistence = _normalise_v2_persistence(item.get("persistence"))
        normalized.append({
            "match_component_id": match_id,
            "family": family,
            "label": label,
            "target": {"type": target_type, "id": str(target.get("id") or "")[:160]},
            "dimensions": {
                "valence": _v2_number(dimensions.get("valence", 0), "valence", -1, 1),
                "arousal": _v2_number(dimensions.get("arousal", .5), "arousal", 0, 1),
                "agency": _v2_number(dimensions.get("agency", 0), "agency", -1, 1),
            },
            "intensity": _v2_number(item.get("intensity", .5), "intensity", 0, 1),
            "persistence": persistence,
            "reason": str(item.get("reason") or "")[:500],
            "appraisal": {str(k): str(v)[:40] for k, v in appraisal.items() if str(k) in {
                "novelty", "goal_congruence", "certainty", "relational_significance"
            }},
            "evidence": normalized_evidence,
        })
    if operation == "no_change" and normalized:
        raise AffectValidationError("no_change cannot carry components")
    if operation != "no_change" and not normalized:
        raise AffectValidationError("state-changing operation requires components")
    return {
        "operation": operation,
        "owner": owner,
        "components": normalized,
        "explicitness": explicitness,
        "confidence": confidence,
        "time_scope": time_scope,
        "reason": str(data.get("reason") or "")[:500],
    }


__all__ = [
    "AffectValidationError", "parse_flash_output", "parse_flash_output_v2",
    "OPERATIONS", "EXPLICITNESS", "TIME_SCOPES", "TIME_SCOPE_ALIASES", "V2_OPERATIONS", "OWNERS",
    "EXPLICITNESS_ALIASES",
]
