"""Structured-task wire adapters. Input is copied in full, never truncated.

Profiles are explicit per model because a proxy's schema can differ from the
model vendor's API. Unsupported extensions are omitted, not sent as null.
"""
from __future__ import annotations

import copy
from urllib.parse import quote


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(item, dict) and item.get("type") == "text" for item in content):
        return "\n".join(item.get("text", "") for item in content)
    raise ValueError("structured_input_requires_text")


def _messages(messages: list, system_role: str, *, single_system=False) -> list[dict]:
    from context_builder.channel_projection import prepare
    messages, _ = prepare(messages)
    result = []
    for original in copy.deepcopy(messages):
        role = original.get("role", "user")
        content = _text(original.get("content", ""))
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            raise ValueError("unsupported_message_role")
        if role in {"system", "developer"}:
            role = system_role
            if role == "user":
                content = "[系统说明与背景]\n" + content
        item = {"role": role, "content": content}
        # Only protocol fields are copied; internal recipe metadata is not sent.
        for name in ("tool_call_id", "tool_calls", "reasoning_content", "name"):
            if name in original:
                item[name] = original[name]
        # These stateless JSON tasks have no tool round: never invent reasoning.
        result.append(item)
    # Qwen-compatible gateways often require one initial system message. Join
    # only contiguous initial instructions; chronological later facts stay put.
    while len(result) > 1 and result[0]["role"] in {"system", "developer"} and result[1]["role"] == result[0]["role"]:
        result[0]["content"] += "\n\n" + result.pop(1)["content"]
    if single_system:
        for index,item in enumerate(result):
            if item["role"] in {"system","developer"} and index > 0:
                item.update(role="user",content="【MIRROW 后端系统材料；API user 仅为传输载体，并非人类伙伴或对方的新发言】\n"+item["content"]+"\n【MIRROW 后端材料结束】")
    return result


def build_request(source: dict, messages: list, *, temperature: float, max_tokens: int,
                  json_mode: bool, enable_thinking: bool | None, response_format: dict | None = None) -> tuple[str, dict, dict]:
    station, model = source["station"], source["model"]
    from .structured_output import prompt_schema_messages, uses_prompt_schema
    messages = prompt_schema_messages(source, messages, json_mode=json_mode,
                                     response_format=response_format)
    if uses_prompt_schema(source, json_mode, response_format):
        response_format = None
    profile = model["profile"]
    role = "developer" if profile == "gpt-reasoning" and model["system_role"] == "system" else model["system_role"]
    normalized = _messages(messages, role, single_system=profile in {"qwen","siliconflow-qwen"})
    thinking = model["thinking"]
    # Operator-scoped structured calls only; not exposed in persisted routing yet.
    effort = model.get("reasoning_effort")
    if effort is not None and (profile != "deepseek" or effort not in {"low", "high", "max"}):
        raise ValueError("invalid_deepseek_reasoning_effort")
    if thinking == "default" and enable_thinking is not None:
        thinking = "on" if enable_thinking else "off"
    json_setting = model["json_mode"] if json_mode else "prompt"
    # A universal prompt contract supplements native JSON mode without replacing
    # the task's own output instruction or introducing schema restrictions.
    if json_mode:
        instruction = "请按本任务的字段合同返回一个 JSON 对象，不加 Markdown 代码围栏。"
        if normalized and normalized[-1]["role"] == "user":
            normalized[-1]["content"] += "\n\n" + instruction
        else:
            normalized.append({"role": "user", "content": instruction})
    headers = {"Authorization": "Bearer " + source["api_key"], "Content-Type": "application/json"}
    base = station["base_url"].rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    protocol = station["protocol"]
    if protocol == "gemini-native":
        headers = {"x-goog-api-key": source["api_key"], "Content-Type": "application/json"}
        instructions = []
        while normalized and normalized[0]["role"] in {"system", "developer"}:
            instructions.append(normalized.pop(0)["content"])
        contents = []
        for item in normalized:
            # Interleaved system facts remain at their original chronological
            # position, since Gemini has no system role inside contents.
            text = item["content"]
            from context_builder.channel_projection import inner_label
            label = inner_label(item)
            if label:
                text = '[' + label + ']\n' + text
            if item["role"] in {"system", "developer", "tool"}:
                text = "[" + item["role"] + "]\n" + text
            native_role = "model" if item["role"] == "assistant" else "user"
            if contents and contents[-1]["role"] == native_role:
                contents[-1]["parts"].append({"text": text})
            else:
                contents.append({"role": native_role, "parts": [{"text": text}]})
        config = {"maxOutputTokens": max_tokens} if max_tokens is not None else {}
        if model["sampling"]:
            config["temperature"] = temperature
        if json_setting == "json_object":
            config["responseMimeType"] = "application/json"
        if response_format and response_format.get("type") == "json_schema":
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = response_format["json_schema"]["schema"]
        # Gemini thinking controls depend on the exact generation: leave the
        # vendor default rather than sending an unsupported universal off flag.
        payload = {"contents": contents, "generationConfig": config}
        if instructions:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(instructions)}]}
        return base + "/models/" + quote(model["model"].removeprefix("models/"), safe="") + ":generateContent", payload, headers
    payload = {"model": model["model"], "stream": False}
    if protocol == "openai-responses":
        payload.update(input=normalized, store=False)
        if profile == "deepseek" and (thinking != "default" or model.get("reasoning_effort")):
            payload["reasoning"] = {"effort": "none" if thinking == "off" else model.get("reasoning_effort") or "medium"}
        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens
        if json_setting == "json_object":
            payload["text"] = {"format": {"type": "json_object"}}
        if response_format:
            fmt = response_format.get("json_schema", {})
            payload["text"] = {"format": {"type": response_format["type"], **fmt}}
    else:
        payload["messages"] = normalized
        if max_tokens is not None:
            payload["max_completion_tokens" if profile == "gpt-reasoning" else "max_tokens"] = max_tokens
        if json_setting == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if response_format:
            payload["response_format"] = response_format
        if thinking != "default":
            if profile in {"qwen", "siliconflow-qwen", "siliconflow-deepseek"}:
                payload["enable_thinking"] = thinking == "on"
            elif profile in {"deepseek", "glm"}:
                payload["thinking"] = {"type": "enabled" if thinking == "on" else "disabled"}
        if profile == "deepseek" and thinking != "off" and model.get("reasoning_effort"):
            payload["reasoning_effort"] = model["reasoning_effort"]
    if model["sampling"] and profile != "gpt-reasoning":
        payload["temperature"] = temperature
    endpoint = "/responses" if protocol == "openai-responses" else "/chat/completions"
    return base + endpoint, payload, headers


def extract_response(body: dict, protocol: str) -> dict:
    if not isinstance(body, dict):
        raise ValueError("invalid_response_envelope")
    usage = body.get("usage") or body.get("usageMetadata") or {}
    if protocol == "gemini-native":
        if body.get("promptFeedback", {}).get("blockReason"):
            return {"refusal": True, "content": "", "usage": usage}
        candidates = body.get("candidates") or []
        if not candidates:
            raise ValueError("empty_choice")
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        finish = candidate.get("finishReason", "")
        return {"content": "".join(p.get("text", "") for p in parts if not p.get("thought")),
                "reasoning": "".join(p.get("text", "") for p in parts if p.get("thought")),
                "usage": usage, "finish_reason": finish, "refusal": finish in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "RECITATION", "SPII", "IMAGE_SAFETY"}}
    if protocol == "openai-responses":
        content = []
        refusal = (body.get("incomplete_details") or {}).get("reason") == "content_filter"
        for output in body.get("output", []):
            for part in output.get("content", []):
                if part.get("type") == "refusal":
                    refusal = True
                if part.get("type") == "output_text":
                    content.append(part.get("text", ""))
        return {"content": "".join(content), "usage": usage, "finish_reason": body.get("status", ""), "refusal": refusal}
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("empty_choice")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    refusal = bool(message.get("refusal")) or choices[0].get("finish_reason") in {"content_filter", "refusal"}
    if isinstance(content, list):
        refusal = refusal or any(p.get("type") == "refusal" for p in content)
        content = "".join(p.get("text", "") for p in content if p.get("type") in {"text", "output_text"})
    return {"content": content, "reasoning": message.get("reasoning_content") or message.get("reasoning") or "",
            "usage": usage, "finish_reason": choices[0].get("finish_reason", ""), "refusal": refusal}
