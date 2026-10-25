"""Account for existing expression/vision transports without changing sources."""
from __future__ import annotations

import asyncio
import logging
import httpx

from . import audit
from .adapters import extract_response
logger = logging.getLogger(__name__)


async def tracked_post(client, url, *, json, headers, task_id, category, node, ordinal, **kwargs):
    handle = None
    try:
        source = await asyncio.to_thread(audit.identify_source, url, json.get("model", ""),
                                        headers.get("Authorization", "").removeprefix("Bearer "))
        handle = await asyncio.to_thread(audit.begin, task_id, category, node, source, ordinal)
    except Exception:
        logger.warning('Direct model request diagnostics unavailable')
    status, error, http_status, usage, trace = "error", "unexpected_response", 0, {}, ""
    try:
        response = await client.post(url, json=json, headers=headers, **kwargs)
        http_status = response.status_code
        trace = getattr(response, "headers", {}).get("x-request-id", "")
        try:
            body = response.json()
        except (ValueError, TypeError):
            body = None
        if isinstance(body, dict):
            reported = body.get('usage') or body.get('usageMetadata')
            if isinstance(reported, dict):
                usage = reported
        if http_status >= 400:
            status = "http_error"
            error = "rate_limited" if http_status == 429 else "authentication_failed" if http_status in {401, 403} else "request_format_error" if http_status == 400 else "provider_http_error"
        else:
            try:
                extracted = extract_response(body, "openai-chat")
                if extracted.get("refusal"):
                    status, error = "refusal", "model_refusal"
                elif not str(extracted.get("content", "")).strip():
                    status, error = "empty_content", "model_returned_no_content"
                else:
                    status, error = "ok", ""
            except Exception:
                status, error = "empty_choice", "invalid_response_envelope"
        return response
    except asyncio.CancelledError:
        status, error = "cancelled", "request_cancelled"
        raise
    except httpx.TimeoutException:
        status, error = "timeout", "request_timeout"
        raise
    except httpx.RequestError:
        status, error = "connection_error", "connection_failed"
        raise
    finally:
        if handle:
            try:
                await asyncio.to_thread(audit.finish, handle, status, error_code=error,
                                        http_status=http_status, trace_id=trace, usage=usage)
            except Exception:
                logger.warning('Direct model response diagnostics unavailable')
