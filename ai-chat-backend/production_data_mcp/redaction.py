from __future__ import annotations

import re
from typing import Any


MAX_TEXT_CHARS = 8_000

SENSITIVE_FIELD = re.compile(
    r"(?:^|_)(?:api_?key|secret|password|passwd|token|cookie|authorization|credential|"
    r"private_?key|client_?secret|encrypted|cipher|session_?hash|key_?hash|submit_?token|"
    r"latitude|longitude|location_(?:lat|lng|address)|home_(?:lat|lng)|work_(?:lat|lng)|"
    r"device_(?:id|serial|mac)|hr_device|wifi|ssid|reasoning|raw_output|prompt|tool_calls?(?!_count))(?:_|$)",
    re.IGNORECASE,
)

PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|client[_-]?secret|password|token)\s*[:=]\s*['\"]?[^\s,'\"]{8,}"),
    re.compile(r"(?i)\bhttps?://[^\s'\"<>]+"),
    re.compile(r"\b[A-Za-z]:\\(?:[^\r\n\\]+\\){1,}[^\r\n]*"),
    re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"),
    re.compile(r"(?i)(?<![0-9a-f:])(?:fc|fd)[0-9a-f:]{3,}(?![0-9a-f:])"),
    re.compile(r"(?<!\w)[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)"),
    re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)[+-]?\d{1,3}\.\d{4,}\s*[,，]\s*[+-]?\d{1,3}\.\d{4,}(?!\d)"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
)


def redact_text(value: str) -> str:
    text = value[:MAX_TEXT_CHARS]
    for pattern in PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def sanitize_value(field: str, value: Any) -> Any:
    if SENSITIVE_FIELD.search(field):
        raise ValueError(f"field is not exportable: {field}")
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    raise ValueError(f"non-scalar value is not exportable: {field}")


def sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: sanitize_value(key, value) for key, value in row.items()}
