"""Preserve existing Flash prompt inspection at the actual provider boundary."""
from copy import deepcopy


def wire_messages(payload, fallback):
    if "messages" in payload:return payload["messages"]
    if "input" in payload:return payload["input"]
    rows=[]
    if payload.get("systemInstruction"):
        rows.append({"role":"system","content":"\n".join(p.get("text","") for p in payload["systemInstruction"].get("parts",[]))})
    for item in payload.get("contents",[]):
        rows.append({"role":"assistant" if item.get("role")=="model" else "user","content":"\n".join(p.get("text","") for p in item.get("parts",[]))})
    return rows or fallback


def begin(payload, canonical, *, model, route, request_id, projection):
    try:
        from context_inspection import observe_provider
        return observe_provider(wire_messages(payload,canonical),model=model,provider=route,use_bound=False,
            source_messages=canonical,request_projection=projection,request_id=request_id)
    except Exception:
        return None


def finish(handle, status):
    try:
        from context_inspection import finish_provider
        finish_provider(handle,status)
    except Exception:
        pass


def usage(payload, canonical, data, *, model, route, billing_provider, duration_ms, request_id, started_at, projection):
    try:
        from prompt_ledger import record_provider_usage_for_messages
        record_provider_usage_for_messages(wire_messages(payload,canonical),data,model=model,provider=route,
            billing_provider=billing_provider,latency_ms=duration_ms,
            request_options={k:deepcopy(v) for k,v in payload.items() if k not in {"messages","input","contents","systemInstruction"}},
            source_messages=canonical,request_projection=projection,
            provider_request_id=request_id,provider_started_at=started_at)
    except Exception:
        pass
