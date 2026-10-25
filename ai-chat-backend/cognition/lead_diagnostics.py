"""Text-free input and proposal counts; not a second cognitive authority."""
from collections import Counter

DIMENSIONS = frozenset({"food", "sleep_rhythm", "work", "leisure", "expression"})


def input_counts(observations, material_ids):
    counts = Counter()
    for lead in observations:
        if not isinstance(lead, dict):
            continue
        dimension = lead.get("lead_dimension")
        bucket = dimension if lead.get("lead_origin") == "habit_radar" and dimension in DIMENSIONS else "other"
        counts[bucket + ":collected"] += 1
        refs = lead.get("reference_ids") or [row.get("id") for row in lead.get("evidence", []) if isinstance(row, dict)]
        valid = {ref for ref in refs if isinstance(ref, str) and ref in material_ids}
        counts[bucket + ":citable_sources"] += len(valid)
        counts[bucket + ":has_citable_source"] += int(bool(valid))
        counts[bucket + ":sent_with_source"] += int(bool(valid) and isinstance(lead.get("text"), str))
    return dict(sorted(counts.items()))


def reviewed_funnel(prepared, approved_count):
    before = prepared.get("lead_funnel")
    if not isinstance(before, dict):
        return {"status": "unavailable_for_old_preparation"}
    return {**before, "reviewed_candidates": len(prepared["entries"]),
            "approved_candidates": approved_count,
            "review_removed": len(prepared["entries"]) - approved_count,
            "commit_stage": "not_attempted_by_analysis"}


def committed_funnel(funnel, items):
    if not isinstance(funnel, dict):
        return
    # Item status is an execution receipt. It never establishes which habit
    # caused the change; names, references and bodies stay outside diagnostics.
    allowed = {"created", "updated", "unchanged", "completed", "rejected", "skipped"}
    funnel["commit_stage"] = "returned_items"
    funnel["returned_items"] = len(items)
    funnel["receipt_status_known"] = all(isinstance(item, dict) for item in items)
    if funnel["receipt_status_known"]:
        funnel["commit_receipts"] = dict(Counter(
            item.get("status") if item.get("status") in allowed else "other" for item in items))
