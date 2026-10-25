"""Read-only recovery selection for interrupted derived work after day commit."""
from .scene_planner import SceneReplayPlanner, scene_start_date
from .scene_reconstruction_contract import geometry
from .scene_reconstruction_store import ReconstructionStore
from .scene_digest_store import cache_path
from .scene_regrouping import needs_regroup


def reconstruction_recovery_dates(source, settled_dates, receipts, *, cutoff):
    start = scene_start_date()
    if not start:
        return set(), set()
    from scene_manager.scene import configured_gap_minutes
    planner = SceneReplayPlanner(source, gap_minutes=configured_gap_minutes())
    store = ReconstructionStore(cache_path(source))
    pending = set()
    completed = set()
    for day in settled_dates:
        receipt = receipts.get(day, {})
        interrupted = receipt.get('status') == 'attempting'
        failed = (receipt.get('status') == 'error' and
                  str(receipt.get('error_code', '')).startswith('scene_reconstruction_'))
        if not (interrupted or failed) or not start <= day < cutoff:
            continue
        boxes = planner.boxes_for_day(day, completed_before=cutoff)
        if not boxes or not all(box.closed for box in boxes):
            continue
        refs = tuple(ref for box in boxes for batch in box.batches for ref in batch.batch_sources)
        parts = store.load(day, refs, source, geometry=geometry(boxes, source, day))
        if parts is None or needs_regroup(parts, active_date=day):
            pending.add(day)
        else:
            completed.add(day)
    return pending, completed
