"""One deterministic body boundary for AI's outgoing natural-language messages.

Call after extracting actions and capturing Affect metadata, before storage,
callbacks or notifications. This never executes actions or changes sources.
The filter inventory and its literal-preservation contract live in
docs/VISIBLE_REPLY_FILTERS.md.
"""
from __future__ import annotations

import json
import re
from typing import Sequence

from affect.sidecar import strip_affect_sidecar
from context_builder.display_markers import strip_display_markers
from context_builder.text_utils import strip_internal_history_markers
from reply_metadata_filter import strip_generated_model_label
from tool_action_protocol import strip_tool_protocol_text

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SPEAKER = re.compile(r"^ {0,3}[Kk][ \t]*[：:][ \t]*")
_INLINE_LITERAL = re.compile(
    r"(`+)(.*?)(?<!`)\1(?!`)|“[^”\r\n]*”|‘[^’\r\n]*’"
    r"""|"(?:\\.|[^"\\\r\n])*"|(?<!\w)'(?:\\.|[^'\\\r\n])*'"""
    r"|!?\[[^\]\r\n]*\](?:\([^\r\n]*?\)|[ \t]*\[[^\]\r\n]*\])"
    r"|^ {0,3}\[[^\]\r\n]+\]:[^\r\n]*"
)
_VOICE = re.compile(r"\[voice:[^\]\r\n]*\]", re.IGNORECASE)
_TOOL_JSON = re.compile(r"^ {0,3}TOOL_CALL[ \t]*[:：](.*)$", re.IGNORECASE)


def _headers(text: str) -> str:
    lines = []
    fence_char, fence_size = "", 0
    leading = True
    for line in text.splitlines(keepends=True):
        fence = _FENCE.match(line)
        if fence_char:
            lines.append(line)
            if fence and fence[1][0] == fence_char and len(fence[1]) >= fence_size and not fence[2].strip():
                fence_char = ""
            continue
        if not line.startswith(("    ", "\t")) and not line.lstrip(" ").startswith(">"):
            ending = line[len(line.rstrip('\r\n')):]
            body = line[:-len(ending)] if ending else line
            previous = None
            while body != previous and not _FENCE.match(body):
                previous = body
                # Finish THIS line before looking at the next: these prefixes
                # can reveal a fence protecting all subsequent literal lines.
                body = strip_generated_model_label(body)
                body = strip_display_markers(body)
                if leading:
                    body = _SPEAKER.sub("", body, count=1)
            line = body + ending
            fence = _FENCE.match(line)
        if fence:
            fence_char, fence_size = fence[1][0], len(fence[1])
        if line.strip():
            leading = False
        lines.append(line)
    return "".join(lines)


def _protect_literals(text: str) -> tuple[str, dict[str, str]]:
    """Protect Markdown code/quotes from the legacy protocol parsers."""
    prefix = "\x00MIRROW_LITERAL_"
    while prefix in text:
        prefix += "_"
    literals: dict[str, str] = {}

    def save(value: str) -> str:
        key = f"{prefix}{len(literals)}\x00"
        literals[key] = value
        return key

    result = []
    fence_char, fence_size = "", 0
    legacy_json_depth = None
    for line in text.splitlines(keepends=True):
        fence = _FENCE.match(line)
        ending = line[len(line.rstrip('\r\n')):]
        body = line[:-len(ending)] if ending else line
        if fence_char:
            result.append(save(body) + ending)
            if fence and fence[1][0] == fence_char and len(fence[1]) >= fence_size and not fence[2].strip():
                fence_char = ""
        elif legacy_json_depth is not None and (legacy_json_depth != 0 or body.lstrip().startswith("{")):
            # A known control block owns its JSON indentation. Shielding its
            # closing brace as Markdown would make the protocol parser eat the
            # natural paragraph AFTER the object as well.
            legacy_json_depth += body.count("{") - body.count("}")
            if legacy_json_depth <= 0 and "}" in body:
                legacy_json_depth = None
            result.append(line)
        elif tool := _TOOL_JSON.match(body):
            after = tool[1]
            depth = after.count("{") - after.count("}")
            legacy_json_depth = depth if not after.strip() or depth > 0 else None
            result.append(line)
        elif fence:
            legacy_json_depth = None
            fence_char, fence_size = fence[1][0], len(fence[1])
            result.append(save(body) + ending)
        elif line.startswith(("    ", "\t")) or line.lstrip(" ").startswith(">"):
            legacy_json_depth = None
            result.append(save(body) + ending)
        else:
            legacy_json_depth = None
            result.append(_INLINE_LITERAL.sub(lambda match: save(match[0]), line))
    return "".join(result), literals


def strip_voice_markers(content: str) -> str:
    """Voice-parser compatibility without removing tool actions before parsing."""
    text, literals = _protect_literals(str(content or ""))
    text = _VOICE.sub("", text)
    for key, value in literals.items():
        text = text.replace(key, value)
    return text


def clean_visible_reply(content: object, *, known_tool_names: Sequence[str] | None = None) -> str:
    """Return the same visible body for persistence, push and notification.

    Sidecar capture belongs to the caller; this final guard only discards any
    remaining reserved block. Ordinary brackets, emojis and bare times stay.
    """
    text = _headers(strip_affect_sidecar(content))
    # Check the intact object before shielding quoted/indented JSON lines as
    # literal Markdown; otherwise a pretty-printed call would stop parsing.
    try:
        bare = json.loads(text)
        if isinstance(bare, dict) and bare.get("tool") and isinstance(bare.get("parameters"), dict):
            return ""
    except (TypeError, ValueError):
        pass
    text, literals = _protect_literals(text)
    previous = None
    while text != previous:
        previous = text
        text = _headers(text)
        text = strip_internal_history_markers(text)
        text = strip_tool_protocol_text(text, known_tool_names)
        text = _VOICE.sub("", text)
    for key, value in literals.items():
        text = text.replace(key, value)
    # A natural-language envelope may have revealed a bare object only after
    # the literal strings were shielded. Recheck the restored complete text.
    try:
        bare = json.loads(text)
        if isinstance(bare, dict) and bare.get("tool") and isinstance(bare.get("parameters"), dict):
            return ""
    except (TypeError, ValueError):
        pass
    return text.strip("\r\n") if text.strip() else ""
