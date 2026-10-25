"""Count SDK transport attempts explicitly, preserving one selected brain source."""
from __future__ import annotations

import asyncio
import uuid

from . import audit


class ProviderRefusal(RuntimeError):
    provider_refusal = True


async def call_sdk(client, request_params: dict, *, api_url: str, api_key: str, node: str = "main_chat"):
    # The wrapper owns projection and observes the submitted request. Sending
    # it unchanged preserves explicit baseline mode and the observation proof.
    request_params = dict(request_params)
    from openai import AsyncOpenAI, APIConnectionError
    # SDK internal retries hide failed attempts. Keep the same two attempts at
    # this boundary, always on the selected endpoint, so both are observable.
    actual = client.with_options(max_retries=0) if isinstance(client, AsyncOpenAI) else client
    from .main_route import attempt_identity
    identity = attempt_identity()
    source = identity["source"] if identity else await asyncio.to_thread(audit.identify_source, api_url, request_params["model"], api_key)
    task_id = identity["task_id"] if identity else uuid.uuid4().hex
    attempts = 1 if identity and not identity.get("terminal", True) else 2
    for ordinal in range(1, attempts + 1):
        handle = await asyncio.to_thread(audit.begin, task_id, "main", identity["node"] if identity else node, source, identity["ordinal"] if identity else ordinal)
        try:
            response = await actual.chat.completions.create(**request_params)
            message = response.choices[0].message
            refusal = bool(getattr(message, "refusal", None)) or getattr(response.choices[0], "finish_reason", "") in {"content_filter", "refusal"}
            status = "refusal" if refusal else "ok" if message.content or getattr(message, "tool_calls", None) else "empty_content"
            if identity and identity.get('routing_source'):
                from .availability import record_outcome
                record_outcome(identity['routing_source'], status, http_status=200)
            usage = response.usage.model_dump() if getattr(response, "usage", None) and hasattr(response.usage, "model_dump") else {}
            await asyncio.to_thread(audit.finish, handle, status, http_status=200,
                                    trace_id=getattr(response, "_request_id", "") or "", usage=usage)
            return response
        except asyncio.CancelledError:
            await asyncio.to_thread(audit.finish, handle, "cancelled", error_code="request_cancelled")
            raise
        except Exception as exc:
            code = getattr(exc, "status_code", 0) or 0
            body = getattr(exc, "body", None)
            error = body.get("error", body) if isinstance(body, dict) else {}
            if isinstance(error, dict) and error.get("code") in {"content_filter", "content_policy_violation", "safety_violation"}:
                await asyncio.to_thread(audit.finish, handle, "refusal", http_status=code, error_code="provider_refusal")
                raise ProviderRefusal("provider_refusal") from None
            if identity and identity.get('routing_source') and (code or isinstance(exc, APIConnectionError)):
                from .availability import record_outcome
                record_outcome(identity['routing_source'], 'http_error' if code else 'connection_error', http_status=code)
            await asyncio.to_thread(audit.finish, handle, "http_error" if code else "connection_error", http_status=code,
                                    error_code="rate_limited" if code == 429 else "main_provider_error")
            if ordinal == attempts or (code and code not in {408, 409, 429} and code < 500):
                raise
            await asyncio.sleep(0.5)
