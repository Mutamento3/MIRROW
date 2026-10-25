"""Fail over one model decision, never replay a scheduler/tool invocation."""
from __future__ import annotations
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import uuid

_LEASE = ContextVar("main_source_lease", default=None)
_ATTEMPT = ContextVar("model_attempt_identity", default=None)


@dataclass
class MainLease:
    sources: list[dict]
    timeout: int
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    index: int = 0
    receipt: dict | None = None
    message_sources: dict = field(default_factory=dict)
    adapters: dict = field(default_factory=dict)
    manual: bool = False


def attempt_identity():
    return _ATTEMPT.get()


def manual_selection_active():
    lease = _LEASE.get()
    return bool(lease and lease.manual)


def _manual_source(profile):
    return {
        "station": {"id": profile.station_id or "main_" + profile.id,
                    "name": profile.station_name or profile.label,
                    "base_url": profile.base_url, "protocol": "openai-chat", "enabled": True},
        "model": {"id": profile.id, "label": profile.label, "model": profile.model,
                  "profile": "gemini" if "gemini" in profile.id else "generic", "enabled": True},
        "api_key": profile.api_key, "ordinal": 1,
        "selection": {"managed": False, "manual": True},
    }


def current_source():
    lease = _LEASE.get()
    return dict(lease.receipt) if lease and lease.receipt else None


def message_source_snapshot():
    lease = _LEASE.get()
    return dict(lease.message_sources) if lease else {}


def bind_message_source(data: dict) -> None:
    lease = _LEASE.get()
    receipt = current_source()
    if receipt and lease:
        data["modelSource"] = receipt
        if data.get("message_id"):
            lease.message_sources[str(data["message_id"])] = receipt


def main_route_scope(function):
    from .scope import routing_scope
    @routing_scope
    @wraps(function)
    async def scoped(*args, **kwargs):
        from .config import resolve_chain
        from .scope import current_snapshot
        from main_model_profiles import DEFAULT_PROFILE_ID, get_main_model_profile
        scheduler = args[0] if args else kwargs.get("self")
        selected = getattr(scheduler, "main_model_profile_id", DEFAULT_PROFILE_ID)
        if selected != DEFAULT_PROFILE_ID:
            config, env, _ = current_snapshot()
            profile = get_main_model_profile(selected, env)
            # An explicit alternate owns this whole invocation, including tool
            # continuations. Never resolve/append the category's official chain.
            lease = MainLease([_manual_source(profile)], config.timeout_seconds, manual=True)
        else:
            sources, timeout = await asyncio.to_thread(resolve_chain, "main_chat")
            managed = bool(sources and sources[0].get("selection", {}).get("managed"))
            lease = MainLease(sources, timeout) if managed else None
        token = _LEASE.set(lease)
        try:
            return await function(*args, **kwargs)
        finally:
            lease = _LEASE.get()
            if lease:
                for adapter in lease.adapters.values():
                    client = getattr(adapter._wrapper, "_client", None)
                    if client:
                        try:
                            await client.close()
                        except Exception:
                            pass
            _LEASE.reset(token)
    return scoped


async def execute_main(scheduler, original_adapter, *, vision: bool = False, **request):
    lease = _LEASE.get()
    if lease is None or (vision and not lease.manual):
        if lease:
            lease.receipt = None
        return await original_adapter.execute(**request)
    last = None
    for index in range(lease.index, len(lease.sources)):
        source = lease.sources[index]
        from .availability import readiness
        if not readiness(source, terminal=index == len(lease.sources)-1)['eligible']:
            continue
        if index != lease.index:
            await scheduler._notify_status("routing_switch", {"attempt": index + 1, "total": len(lease.sources), "reason": "source_unavailable"})
        lease.index = index
        station, model = source["station"], source["model"]
        adapter = lease.adapters.get(model["id"])
        if adapter is None:
            adapter = scheduler.build_main_model_adapter("openai-compatible", source["api_key"], station["base_url"], model["model"])
            adapter._wrapper._routing_materials = True
            adapter._wrapper._routing_profile = model["profile"]
            adapter._wrapper._billing_provider = "deepseek" if station["id"] in {"deepseek_main", "deepseek_direct"} else "siliconflow" if station["id"] == "siliconflow" else "unknown"
            adapter._wrapper._routing_timeout = lease.timeout
            lease.adapters[model["id"]] = adapter
        identity = {"task_id": lease.task_id, "node": "main_chat", "routing_source": source, "ordinal": source["ordinal"], "terminal": index == len(lease.sources)-1, "source": {
            "station_id": station["id"], "station_name": station["name"], "source_id": model["id"], "model": model["model"]}}
        token = _ATTEMPT.set(identity)
        try:
            try:
                async with asyncio.timeout(lease.timeout):
                    last = await adapter.execute(**request)
            except TimeoutError:
                from .availability import record_outcome
                record_outcome(source, 'timeout')
                last = {"content": "", "error": "request_timeout"}
        finally:
            _ATTEMPT.reset(token)
        if last.get("refusal"):
            lease.receipt = None
            return last
        if lease.manual and last.get("error"):
            lease.receipt = None
            return last
        # Real tool actions are a valid answer, including action-only turns.
        valid = bool(str(last.get("content") or "").strip() or str(last.get("natural_language") or "").strip() or last.get("tool_call") or last.get("tool_calls") or last.get("action_directives"))
        if not last.get("error") and (valid or index == len(lease.sources)-1):
            if not valid:
                lease.receipt = None
                return last
            lease.receipt = {"station_id": station["id"], "station_name": station["name"], "model": model["model"],
                             "model_label": model["label"], "source_id": model["id"], "ordinal": source["ordinal"]}
            last["model_source"] = current_source()
            return last
        lease.receipt = None
    # A terminal empty answer remains empty. The scheduler owns its existing
    # placeholder policy; do not turn it into refusal or regenerate the turn.
    return {"content": "", "error": "all_sources_unavailable", "routing_failed": True}
