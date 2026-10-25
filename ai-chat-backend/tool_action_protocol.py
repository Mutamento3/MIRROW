"""Model-facing natural-language tool action protocol.

AI emits closed action blocks at the end of a response::

    @eyes 看看人类伙伴现在在做什么@

Blocks may span lines; a multiline block ends with a standalone ``@``.

The parser only recognizes registered tool names (including a unique prefix of
at least three characters).  A missing closing ``@`` is accepted solely for
the final non-empty line, which keeps tolerance local without turning ordinary
mentions into executable commands.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Iterable, Sequence


_CLOSED_ACTION_RE = re.compile(
    r"^\s*[@＠]\s*([A-Za-z][A-Za-z0-9_-]*)\s*(.*?)\s*[@＠]\s*$"
)
_OPEN_ACTION_RE = re.compile(
    r"^\s*[@＠]\s*([A-Za-z][A-Za-z0-9_-]*)(?:\s+(.*?))?\s*$"
)
_CLOSE_ONLY_ACTION_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_-]*)(?:\s+(.*?))?\s*[@＠]\s*$"
)
_EARLY_CLOSE_ACTION_RE = re.compile(
    r"^\s*[@＠]\s*([A-Za-z][A-Za-z0-9_-]*)\s*[@＠]\s+(.+?)\s*$"
)
_TRAILING_CLOSED_ACTION_RE = re.compile(
    r"[@＠]\s*([A-Za-z][A-Za-z0-9_-]*)\s*([^@＠\r\n]*?)\s*[@＠]\s*$"
)
_LEGACY_LINE_RE = re.compile(
    r"^\s*(?:NATURAL[_\s]LANGUAGE\s*[:：]\s*)?"
    r"(?:TOOL_CALL\s*[:：]\s*.*|RESULTS_USED\s*[:：]\s*\[[^\n]*\])\s*$",
    re.IGNORECASE,
)
_RESULTS_USED_LINE_RE = re.compile(r"^\s*RESULTS_USED\s*[:：]\s*\[[^\n]*\]\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ToolActionDirective:
    tool: str
    instruction: str = ""
    closed: bool = True


@dataclass(frozen=True)
class _ActionSpan:
    directive: ToolActionDirective
    start: int
    end: int


def resolve_tool_name(name: str, known_tool_names: Iterable[str]) -> str | None:
    """Resolve an exact/case-insensitive name or one unique 3+ char prefix."""
    raw = str(name or "").strip()
    known = [str(item) for item in known_tool_names if str(item)]
    if raw in known:
        return raw
    case_matches = [item for item in known if item.lower() == raw.lower()]
    if len(case_matches) == 1:
        return case_matches[0]
    if len(raw) >= 3:
        prefix_matches = [item for item in known if item.lower().startswith(raw.lower())]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
    return None


def _parse_action_spans(
    content: str, known_tool_names: Iterable[str] | None,
) -> list[_ActionSpan]:
    """Read one trailing action batch, sharing source spans with cleanup.

    Only cleanup may omit a registry: syntactically closed standalone blocks
    can then be removed from persisted text without authorizing execution.
    """
    lines = content.splitlines(keepends=True)
    if not lines:
        return []
    known = tuple(known_tool_names) if known_tool_names is not None else None
    offsets = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    def resolve(name: str) -> str | None:
        return name if known is None else resolve_tool_name(name, known)

    last_nonempty = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), -1)
    if last_nonempty < 0:
        return []

    found: list[_ActionSpan] = []
    index = last_nonempty
    while index >= 0:
        line = lines[index].rstrip("\r\n")
        if not line.strip():
            index -= 1
            continue
        if _RESULTS_USED_LINE_RE.fullmatch(line):
            index -= 1
            continue
        if line.strip() in {"@", "＠"}:
            closing = index
            index -= 1
            while index >= 0:
                opening_line = lines[index].rstrip("\r\n")
                # A previous closed action is a different block, not part of
                # the body of a later malformed block.
                if (opening_line.strip() in {"@", "＠"}
                        or _CLOSED_ACTION_RE.match(opening_line)
                        or _EARLY_CLOSE_ACTION_RE.match(opening_line)):
                    break
                opening = _OPEN_ACTION_RE.match(opening_line)
                if opening:
                    resolved = resolve(opening.group(1))
                    if resolved:
                        body_start = (offsets[index] + opening.start(2)
                                      if opening.group(2) is not None
                                      else offsets[index] + len(lines[index]))
                        instruction = content[body_start:offsets[closing]].strip()
                        found.append(_ActionSpan(
                            ToolActionDirective(resolved, instruction, True),
                            offsets[index], offsets[closing] + len(lines[closing]),
                        ))
                        index -= 1
                        break
                    return list(reversed(found))
                index -= 1
            else:
                break
            # A successfully parsed block has already moved before its opener.
            if found and found[-1].end == offsets[closing] + len(lines[closing]):
                continue
            break

        # Retain the established "@eyes@ 看看现在" compatibility.
        early_close = _EARLY_CLOSE_ACTION_RE.match(line)
        match = early_close or _CLOSED_ACTION_RE.match(line)
        closed = not bool(early_close)
        if not match and index == last_nonempty and known is not None:
            # Compatibility only: the final standalone line may omit exactly
            # one delimiter.  Registry resolution is mandatory in both cases.
            match = _CLOSE_ONLY_ACTION_RE.match(line) or _OPEN_ACTION_RE.match(line)
            closed = False
        if not match:
            remaining = line.rstrip()
            while known is not None and remaining:
                suffix = _TRAILING_CLOSED_ACTION_RE.search(remaining)
                if not suffix:
                    break
                resolved = resolve(suffix.group(1))
                if not resolved:
                    break
                found.append(_ActionSpan(
                    ToolActionDirective(resolved, (suffix.group(2) or "").strip(), True),
                    offsets[index] + suffix.start(), offsets[index] + len(remaining),
                ))
                remaining = remaining[:suffix.start()].rstrip()
            break
        resolved = resolve(match.group(1))
        if not resolved:
            break
        instruction = (match.group(2) or "").strip()
        found.append(_ActionSpan(
            ToolActionDirective(resolved, instruction, closed),
            offsets[index], offsets[index] + len(lines[index]),
        ))
        index -= 1
    found.reverse()
    return found


def extract_tool_actions(
    content: str,
    known_tool_names: Iterable[str],
) -> list[ToolActionDirective]:
    """Extract only registered actions from the trailing action batch."""
    return [span.directive for span in _parse_action_spans(str(content or ""), known_tool_names)]


def strip_tool_protocol_text(
    content: object,
    known_tool_names: Sequence[str] | None = None,
) -> str:
    """Remove model-only tool protocols from assistant-visible/history text."""
    text = str(content or "")
    # Remove the old paired block atomically before line processing; removing
    # only its tags would leave the JSON body available for later context.
    text = re.sub(
        r"\[TOOL_CALL\].*?\[/TOOL_CALL\]",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    known = tuple(known_tool_names or ())
    for span in reversed(_parse_action_spans(text, known or None)):
        text = text[:span.start] + text[span.end:]
    lines = text.splitlines(keepends=True)
    cleaned: list[str] = []
    legacy_json_depth: int | None = None
    for index, line in enumerate(lines):
        if legacy_json_depth is not None:
            stripped = line.strip()
            if legacy_json_depth == 0 and not stripped.startswith("{"):
                legacy_json_depth = None
            else:
                legacy_json_depth += line.count("{") - line.count("}")
                if legacy_json_depth <= 0 and "}" in line:
                    legacy_json_depth = None
                continue
        if re.match(r"^\s*TOOL_CALL\s*[:：]", line, flags=re.IGNORECASE):
            after = re.split(r"TOOL_CALL\s*[:：]", line, maxsplit=1, flags=re.IGNORECASE)[-1]
            depth = after.count("{") - after.count("}")
            legacy_json_depth = depth if (not after.strip() or depth > 0) else None
            continue
        if _LEGACY_LINE_RE.match(line):
            continue
        # Closed directives are safe to identify without a registry because
        # both delimiters and the standalone-line contract are present.
        if _CLOSED_ACTION_RE.match(line):
            continue
        cleaned.append(line)
    text = "".join(cleaned)
    text = re.sub(r"^ {0,3}NATURAL[_ \t]LANGUAGE[ \t]*[:：][ \t]*", "", text, flags=re.IGNORECASE | re.MULTILINE)
    try:
        bare = json.loads(text.strip())
        if isinstance(bare, dict) and bare.get("tool") and isinstance(bare.get("parameters"), dict):
            return ""
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return text.strip("\r\n") if text.strip() else ""


__all__ = [
    "ToolActionDirective",
    "extract_tool_actions",
    "resolve_tool_name",
    "strip_tool_protocol_text",
]
