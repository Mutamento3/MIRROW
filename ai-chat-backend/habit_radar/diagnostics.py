"""Read-only local review, always revalidating both source and AI context."""
import json

from .aggregation import material_windows
from .service import day_material, revision_for
from .source_lifecycle import filter_current, review_material


def status(store, source, *, limit=30, sentinel=None):
    days, signals = [], []
    for row in store.days()[:limit]:
        report = json.loads(row["report"])
        items = day_material(source, row["active_date"])
        fresh = (row["status"] == "complete" and
                 revision_for(items,
                              report.get("model_identity", "")) == row["revision"])
        candidates = store.signals_for(row["active_date"])
        current, stale = filter_current(candidates, source)
        signals.extend(current)
        days.append({key: row[key] for key in
                     ("active_date", "status", "attempts", "next_retry", "updated_at", "error_code")}
                    | {"report": report, "source_snapshot_current": fresh,
                       "current_signals": len(current),
                       "stale_reasons": stale})
        if sentinel:
            calendar_dates = sorted({i.calendar_date for i in items})
            evidence = []
            for day in calendar_dates:
                try:
                    facts = sentinel.read_date(day)
                    evidence.append(facts.safe_report() if facts else
                                    {"summary_date_key": day, "summary_present": False})
                except Exception:
                    evidence.append({"summary_date_key": day, "status": "unavailable"})
            days[-1]["sentinel_field_availability"] = evidence
    from dataclasses import asdict
    return {"days": days, "window_active_day_limit": limit,
            "dimensions": [asdict(w) for w in material_windows(signals).values()],
            "cost": store.call_summary(), "candidate_semantics": "unreviewed_quotes_not_habits"}


def review_day(store, source, day, *, offset=0, limit=40):
    signals = store.signals_for(day) if store.path.exists() else ()
    # Validate the whole bounded day before paging, so stale entries cannot leak.
    material, stale = review_material(signals, source)
    return {"active_date": day, "candidate_semantics": "unreviewed_quotes_not_habits",
            "items": material[offset:offset + limit], "total": len(material),
            "offset": offset, "limit": limit, "stale_reasons": stale}
