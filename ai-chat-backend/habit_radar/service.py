"""Completed-day shadow extraction with reusable batch receipts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from memory_v2.conversation_source import is_canonical_session_id
from .day_material import batch_items, select_day_items
from .extraction import SYSTEM_PROMPT, extract_batches, contract_for
from .source_lifecycle import filter_current


CONTRACT_VERSION = "shadow-quotes-v1"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def day_material(source, day):
    rows = source.read_active_date(day, roles=("user", "assistant"))
    sessions = {r.session_id for r in rows if is_canonical_session_id(r.session_id)}
    if len(sessions) > 1:
        raise ValueError("ambiguous_private_session")
    return (select_day_items(rows, session_id=next(iter(sessions)), active_date=day)
            if sessions else ())


def revision_for(items, model_identity):
    day = max((i.active_date or i.calendar_date for i in items), default='')
    version, prompt = contract_for(day)
    return digest([version, prompt, model_identity, [asdict(i) for i in items]])


async def production_model(system, payload):
    from wander_manager.flash_structured import call_flash_json_detailed
    response = await call_flash_json_detailed(
        [{"role": "system", "content": system}, {"role": "user", "content": payload}],
        temperature=0.0, max_tokens=5000, enable_thinking=False, route_label="habit_radar_shadow")
    # Never retain request_messages/raw_content/reasoning or exception strings.
    usage = {key: response.usage.get(key) for key in ("prompt_tokens", "completion_tokens")}
    usage.update(provider=response.provider, model=response.model)
    return response.parsed if response.status == "ok" else None, usage


async def run_day(source, store, day, *, completed_before, model=production_model,
                  model_identity="structured-default", clock=time.time):
    if day >= completed_before:
        raise ValueError("active_day_not_complete")
    items = day_material(source, day)
    batches = batch_items(items)
    revision = revision_for(items, model_identity)
    now = clock()
    attempt_day = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()
    if not store.claim(day, revision, now=now, attempt_day=attempt_day):
        return {"active_date": day, "status": "unchanged_or_deferred", "model_calls": 0}
    signals, keys = [], []
    report = {"source_messages": len(items), "batches": len(batches), "model_calls": 0,
              "reused_batches": 0, "rejection_reasons": {}, "model_identity": model_identity,
              "first_source_at": items[0].timestamp if items else None,
              "last_source_at": items[-1].timestamp if items else None}
    rejects = Counter()
    try:
        # One day has a hard execution ceiling shorter than the durable lease.
        async with asyncio.timeout(1800):
            for batch in batches:
                key = revision_for(batch.items, model_identity)
                cached = store.cached(key)
                if cached:
                    found, evaluation = cached
                    report["reused_batches"] += 1
                else:
                    async def call(system, payload):
                        report["model_calls"] += 1
                        try:
                            raw, usage = await model(system, payload)
                        except BaseException:
                            store.record_call(day, key, "interrupted_or_failed", {}, now=clock())
                            raise
                        store.record_call(day, key, "ok" if isinstance(raw, dict) else "failed",
                                          usage, now=clock())
                        return raw
                    found, evaluation = await extract_batches([batch], call, active_date=day)
                    store.save_batch(key, found, evaluation, now=clock())
                keys.append(key)
                signals.extend(found)
                rejects.update(evaluation["rejection_reasons"])
            # Also detects deletion/addition of sources that produced NO signals.
            if revision_for(day_material(source, day), model_identity) != revision:
                raise RuntimeError("source_changed_during_extraction")
            valid, stale = filter_current(signals, source)
            if stale:
                raise RuntimeError("source_changed_during_extraction")
            report.update(accepted=len(valid), dimensions=dict(Counter(s.dimension for s in valid)),
                          rejection_reasons=dict(rejects))
            store.finish(day, revision, now=clock(), keys=keys, report=report)
        return {"active_date": day, "status": "complete", **report}
    except BaseException as exc:
        code = ("cancelled" if isinstance(exc, asyncio.CancelledError) else
                "source_changed" if str(exc) == "source_changed_during_extraction" else
                "timeout" if isinstance(exc, TimeoutError) else "extraction_failed")
        report["rejection_reasons"] = dict(rejects)
        store.finish(day, revision, now=clock(), report=report, error=code)
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        return {"active_date": day, "status": "error", "error_code": code, **report}
