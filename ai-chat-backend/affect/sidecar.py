"""AFFECT response-tail sidecar parser.

The sidecar is deliberately a small transport detail.  It is parsed at the
shared scheduler output boundary, removed before a reply is shown/stored, and
only becomes an Affect observation after the final assistant message has been
committed with a real message id.  A malformed optional block is therefore a
diagnostic, never a reason to retry or fail the visible reply.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

OPEN = "⟦MIRROW:AFFECT⟧"
CLOSE = "⟦/MIRROW:AFFECT⟧"
PERSISTENCE = {"momentary", "situational", "lingering"}
OPERATIONS = {"add", "reinforce", "reappraise", "resolve", "no_change"}
FAMILIES = {"愉悦", "亲近", "安定", "兴趣", "低落", "担忧", "恼怒", "排斥", "惊讶", "复杂/未归类"}
_FAMILY_ALIASES = {"复杂未归类": "复杂/未归类", "复杂/未归类": "复杂/未归类"}
_MAX_JSON = 4000


@dataclass(frozen=True)
class SidecarResult:
    clean_content: str
    payload: Optional[dict[str, Any]] = None
    valid: bool = False
    found: bool = False
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        return bool(self.valid and self.payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean_content": self.clean_content,
            "payload": self.payload,
            "valid": self.valid,
            "found": self.found,
            "diagnostics": list(self.diagnostics),
        }


def _bounded_text(value: Any, cap: int) -> str:
    return str(value or "").strip()[:cap]


def _normalise_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("payload must be an object")
    allowed = {"operation", "feeling", "family", "intensity", "reason", "target", "persistence"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError("unknown field")
    operation = _bounded_text(data.get("operation"), 24)
    if operation not in OPERATIONS:
        raise ValueError("invalid operation")
    feeling = _bounded_text(data.get("feeling"), 80)
    family_raw = _bounded_text(data.get("family"), 40)
    family = _FAMILY_ALIASES.get(family_raw, family_raw)
    if family not in FAMILIES:
        # Family is an index for rendering, never a reason to reject AI's
        # visible reply. Unknown model wording falls back to the complex
        # bucket instead of expanding the taxonomy.
        family = "复杂/未归类"
    try:
        intensity = float(data.get("intensity", 0.6))
    except (TypeError, ValueError) as exc:
        raise ValueError("intensity must be a number") from exc
    if intensity != intensity or intensity < 0 or intensity > 1:
        raise ValueError("intensity must be between 0 and 1")
    reason = _bounded_text(data.get("reason"), 500)
    target = _bounded_text(data.get("target"), 120)
    persistence = _bounded_text(data.get("persistence"), 20) or "situational"
    if persistence not in PERSISTENCE:
        raise ValueError("invalid persistence")
    if operation in {"add", "reinforce", "reappraise"} and not feeling:
        raise ValueError("feeling is required")
    if operation == "resolve" and not (feeling or target):
        raise ValueError("resolve needs feeling or target")
    return {
        "operation": operation,
        "feeling": feeling,
        "family": family,
        "intensity": round(intensity, 4),
        "reason": reason,
        "target": target,
        "persistence": persistence,
    }


def _remove_reserved_regions(text: str) -> str:
    """Remove reserved blocks even when malformed so they cannot leak.

    Only a well-formed trailing block is accepted.  For every other shape we
    still remove the reserved region (or the unterminated suffix) from the
    visible content.  This is intentionally conservative for a high-
    temperature model: losing an optional internal note is safer than
    exposing a control block to the conversation or memory pipeline.
    """
    result = text
    while OPEN in result:
        start = result.find(OPEN)
        end = result.find(CLOSE, start + len(OPEN))
        if end < 0:
            result = result[:start].lstrip("\r\n").rstrip()
            break
        result = (result[:start] + result[end + len(CLOSE):]).lstrip("\r\n").rstrip()
    # A closing marker without an opening marker is just as reserved as a
    # malformed block.  It must never leak into visible chat/memory text.
    return result.replace(CLOSE, "").lstrip("\r\n").rstrip()


def parse_affect_sidecar(content: Any) -> SidecarResult:
    """Parse at most one strict trailing sidecar and return safe text.

    No exception escapes for model output.  The caller can log the diagnostic
    and continue with ``clean_content``.
    """
    text = str(content or "")
    if OPEN not in text and CLOSE not in text:
        return SidecarResult(clean_content=text, found=False)

    diagnostics: list[str] = []
    starts = [m.start() for m in re.finditer(re.escape(OPEN), text)]
    ends = [m.start() for m in re.finditer(re.escape(CLOSE), text)]
    # Exactly one complete block, with only whitespace after it, is accepted.
    start = starts[-1] if starts else -1
    end = text.find(CLOSE, start + len(OPEN)) if start >= 0 else -1
    trailing = start >= 0 and end >= 0 and not text[end + len(CLOSE):].strip()
    if len(starts) != 1 or len(ends) != 1:
        diagnostics.append("multiple_or_unbalanced_blocks")
    elif not trailing:
        diagnostics.append("sidecar_not_at_reply_end")
    if start < 0:
        diagnostics.append("closing_marker_without_opening")
        return SidecarResult(
            clean_content=_remove_reserved_regions(text), found=True,
            diagnostics=tuple(diagnostics),
        )
    if end < 0:
        diagnostics.append("unterminated_block")
        return SidecarResult(
            clean_content=_remove_reserved_regions(text), found=True,
            diagnostics=tuple(diagnostics),
        )
    body = text[start + len(OPEN):end].strip()
    clean = (text[:start] + text[end + len(CLOSE):]).lstrip("\r\n").rstrip()
    if len(body) > _MAX_JSON:
        diagnostics.append("payload_too_large")
    try:
        data = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError):
        diagnostics.append("invalid_json")
        data = None
    payload = None
    valid = False
    if data is not None:
        try:
            payload = _normalise_payload(data)
            valid = not diagnostics
        except ValueError as exc:
            diagnostics.append(str(exc))
    if not valid:
        # Remove all reserved fragments, not just the candidate one, because a
        # malformed duplicate must never be rendered or persisted as prose.
        clean = _remove_reserved_regions(text)
        payload = None
    return SidecarResult(
        clean_content=clean,
        payload=payload,
        valid=valid,
        found=True,
        diagnostics=tuple(diagnostics),
    )


def bind_sidecar(
    payload: dict[str, Any],
    *,
    user_message_id: str,
    assistant_message_id: str,
    assistant_revision: int = 1,
    session_id: str = "",
) -> dict[str, Any]:
    """Bind an already validated payload to backend-owned source identity."""
    clean = _normalise_payload(payload)
    if not user_message_id or not assistant_message_id:
        raise ValueError("sidecar binding requires real message ids")
    return {
        "payload": clean,
        "user_message_id": str(user_message_id),
        "assistant_message_id": str(assistant_message_id),
        "assistant_revision": max(1, int(assistant_revision)),
        "session_id": str(session_id or ""),
    }


def strip_affect_sidecar(content: Any) -> str:
    """Convenience helper used by every visible-message persistence path."""
    return parse_affect_sidecar(content).clean_content


__all__ = [
    "OPEN", "CLOSE", "PERSISTENCE", "OPERATIONS", "FAMILIES", "SidecarResult",
    "parse_affect_sidecar", "strip_affect_sidecar", "bind_sidecar",
]
