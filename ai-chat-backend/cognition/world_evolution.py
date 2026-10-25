"""Durable unresolved world material; never an authority for accepted facts.

Consumption happens inside the existing extraction/review pair. Reads do not
promote candidates, and unchanged source runs do not create extra model jobs.
"""
import json
from datetime import datetime
from pathlib import Path

from . import books
from .maintenance_sources import resolve_ref

PIPELINE = "factual-evolution-v2"
UNRESOLVED = {"pending", "observing", "isolated", "error"}
MATERIAL_LIMIT = 20


def legacy_hints():
    """Legacy summaries have no exact source anchors; retain them as hints only."""
    path = books.ROOT / 'world_book' / 'suggestions.json'
    if not path.exists():
        return []
    data = json.loads(path.read_text('utf-8'))
    if not isinstance(data, list) or any(not isinstance(r, dict) for r in data):
        raise books.BookError('历史世界书材料无法读取，保留文件等待核对')
    fields = ('id', 'proposed_name', 'target_entry', 'proposed_body', 'proposed_keywords', 'reason', 'source', 'created')
    return [{key: r.get(key, '') for key in fields} for r in data if r.get('status') == 'pending']


def is_world(item):
    return item.get("proposal", {}).get("domain") == "world"


def collect(records, source_date, session_id):
    """Fair bounded work queue, including same-day and old pending material.

    No lexical relevance gate can permanently hide a record. Everything not
    selected remains durable; successful natural runs rotate the work queue.
    """
    pending = [r for r in records if is_world(r) and r.get("status") in UNRESOLVED
               and r.get("session_id") == session_id and r.get("source_date")
               and r["source_date"] <= source_date]
    pending.sort(key=lambda r: (r.get("last_considered_at", ""), r["created_at"], r["id"]))
    selected = []
    for r in pending[:MATERIAL_LIMIT]:
        p = r["proposal"]
        selected.append({"record_id": r["id"], "source_date": r["source_date"],
                         "status": r["status"], "subject_id": p.get("subject_id", "unknown"),
                         "target_entry": p.get("target_entry") or p.get("name", ""),
                         "candidate_hint": {"name": p.get("name", ""), "body": p.get("body", ""),
                                            "reason": r.get("reason", "")},
                         "source_bundle": bool(r.get("source_bundle_id") and not p.get("evidence")),
                         "evidence": material_evidence(r)})
    return selected, [r["id"] for r in pending[MATERIAL_LIMIT:]]


def fallback_plan(proposal):
    return {"domain": "world", "name": proposal.get("target_entry") or proposal.get("name") or "待重整材料",
            "before": "", "after": proposal.get("body", ""), "revision": "unresolved"}


def material_evidence(item):
    from . import maintenance as m
    refs = item.get("proposal", {}).get("evidence", [])
    if refs or not item.get("source_bundle_id"):
        return rehydrate_legacy(item, refs)
    bundle = item["source_bundle_id"]
    m.record_path(bundle)  # same strict hex identifier check; no arbitrary paths
    return json.loads((m.folder() / "world_sources" / (bundle + ".json")).read_text('utf-8'))


def rehydrate_legacy(item, refs, fetch=None):
    """Recover complete canonical context by exact ID, never by fuzzy text.

    Tests and isolated roots do not read production. Missing/deleted messages,
    changed quotes or different actors/sessions remain partial source clues.
    """
    if all(r.get('source_text') and r.get('source_complete') is not False for r in refs):
        return refs
    if fetch is None:
        if books.ROOT != Path(__file__).resolve().parents[1]:
            return refs
        from event_chronicle import get_global_chronicle
        fetch = get_global_chronicle().get_message_by_msg_id
    from .maintenance_sources import sources
    result = []
    for ref in refs:
        current = dict(ref)
        if not ref.get('source_text') or ref.get('source_complete') is False:
            try:
                row = fetch(ref['message_id'])
                if row and row.get('session_id') == item.get('session_id'):
                    source = next((e for e in sources([row]) if e['id'] == ref['message_id']), None)
                    if (source and source['speaker'] == ref.get('speaker')
                            and ref.get('quote') and ref['quote'] in source['text']):
                        current.update(source_text=source['text'], source_revision=books.revision(source['text']),
                                       source_complete=True, source_kind=source.get('source_kind', ''),
                                       identity_basis=source.get('identity_basis', ''), timestamp=source.get('timestamp', ''))
            except Exception:
                # Retrieval failure is uncertainty, not evidence of no material.
                pass
        result.append(current)
    return result


def isolate(raw, space, *, source_date, session_id, run_id, source_index, reason):
    """Keep the candidate verbatim and only verified source material as evidence.

    A model's bad quote is retained in raw_candidate, never promoted to a source
    quote. Source text comes directly from the referenced input message instead.
    """
    from . import maintenance as m
    candidate = raw if isinstance(raw, dict) else {}
    refs, seen = [], set()
    evidence = candidate.get("evidence", [])
    for ref in evidence if isinstance(evidence, list) else []:
        if not isinstance(ref, dict):
            continue
        msg = resolve_ref(ref, space)
        if not msg or msg["id"] in seen:
            continue
        seen.add(msg["id"])
        refs.append({"message_id": msg["id"], "quote": msg["text"], "speaker": msg["speaker"],
                     "timestamp": msg.get("timestamp", ""), "identity_basis": msg.get("identity_basis", ""),
                     "source_kind": msg.get("source_kind", ""), "source_text": msg["text"],
                     "source_revision": books.revision(msg["text"]),
                     "source_complete": msg.get("source_complete", True),
                     "complete_statement": msg.get("source_complete", True)})
    p = {**candidate, "domain": "world", "name": candidate.get("name") if isinstance(candidate.get("name"), str) else "待重整材料",
         "target_entry": candidate.get("target_entry") if isinstance(candidate.get("target_entry"), str) else "",
         "body": candidate.get("body") if isinstance(candidate.get("body"), str) else json.dumps(raw, ensure_ascii=False),
         "evidence": refs}
    rid = m.digest({"world_isolation": run_id, "source_index": source_index, "candidate": raw})
    if not m.record_path(rid).exists():
        bundle = m.folder() / "world_sources" / (run_id + ".json")
        if not refs and not bundle.exists():
            # One trusted source bundle per run, shared by malformed candidates.
            # This stores evidence only, never books, prompts or model reasoning.
            materials = [{"message_id": e["id"], "quote": e["text"], "source_text": e["text"],
                          "source_revision": books.revision(e["text"]),
                          "source_complete": e.get("source_complete", True),
                          "speaker": e["speaker"], "timestamp": e.get("timestamp", ""),
                          "identity_basis": e.get("identity_basis", ""), "source_kind": e.get("source_kind", "")}
                         for e in space]
            books.atomic_text(bundle, json.dumps(materials, ensure_ascii=False))
        m.write({"id": rid, "status": "isolated", "source_date": source_date, "session_id": session_id,
                 "created_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
                 "proposal": p, "raw_candidate": raw, "review": {}, "reason": reason,
                 "plan": fallback_plan(p), "evolution_version": 2, "run_id": run_id,
                 "source_index": source_index, "input_source_ids": [e["id"] for e in space],
                 **({"source_bundle_id": run_id} if not refs else {})})
    return rid


def finish_materials(state, item):
    """Retire only explicitly reviewed, source-covered material after a commit.

    This is recoverable from the commit receipt. A crash cannot resolve material
    before the book write, or delete its original candidate and source quotes.
    """
    from . import maintenance as m
    if item.get("status") not in {"auto_applied", "unchanged"}:
        return
    p = item["proposal"]
    requested = p.get("resolves_observations", [])
    approved = item.get("review", {}).get("resolved_observation_ids", [])
    if (not isinstance(requested, list) or not isinstance(approved, list)
            or any(not isinstance(x, str) for x in [*requested, *approved])):
        return
    covered = {r["message_id"] for r in p["evidence"]}
    selected = {o["record_id"]: o for o in state.get("historical_observations", [])}
    for rid in dict.fromkeys(requested):
        observation = selected.get(rid)
        if rid not in approved or not observation or rid == item["id"]:
            continue
        refs = observation.get("evidence", [])
        sources = {r["message_id"] for r in refs}
        source_covered = bool(sources & covered) if observation.get("source_bundle") else sources.issubset(covered)
        if not refs or not source_covered:
            continue
        old = m.get(rid)
        if old.get("status") not in UNRESOLVED or old.get("session_id") != item["session_id"]:
            continue
        old_p = old["proposal"]
        if old_p.get("subject_id", "unknown") not in {"unknown", p["subject_id"]}:
            continue
        old_name = old_p.get("target_entry") or old_p.get("name")
        names = {books.title_key(item["plan"]["name"]), books.title_key(p["name"])}
        if not isinstance(old_name, str) or books.title_key(old_name) not in names:
            continue
        old.update(status="superseded", resolved_by=item["id"],
                   resolved_at=item["completed_at"], result_revision=item["result_revision"])
        m.write(old)


def mark_considered(state, *, considered_at):
    """Rotate reviewed world material independently of whole-run completion."""
    from . import maintenance as m
    for observation in state.get("historical_observations", []):
        rid = observation["record_id"]
        item = m.get(rid)
        if item.get("status") in UNRESOLVED:
            item.update(last_considered_at=considered_at, last_considered_run=state["id"])
            m.write(item)
