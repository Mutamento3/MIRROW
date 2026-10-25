"""Read-only completion and drift checks against durable source manifests."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from .replay import ReplayDayPlan


@dataclass(frozen=True)
class DaySourceCoverage:
    source_changed: bool = False
    zero_event_complete: bool = False
    repaired_complete: bool = False
    manifest_complete: bool = False


def inspect_day_source_coverage(
    memory_db_path: str | Path, day: ReplayDayPlan, *,
    source_namespace: str, authority_digest: str,
) -> DaySourceCoverage:
    """Compare the latest promoted generation, never a union of old revisions.

    A repair receipt also proves completion when the repaired day has no rows
    or events. Ordinary zero-event completion is proven by all durable batch
    manifests exactly matching the current source, including content digests.
    """
    uri = Path(memory_db_path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        has_receipts = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='day_repair_receipts'"
        ).fetchone() is not None
        receipt = connection.execute(
            'SELECT authority_digest FROM day_repair_receipts '
            'WHERE source_namespace=? AND active_date=? '
            'ORDER BY created_at DESC, rowid DESC LIMIT 1',
            (source_namespace, day.active_date),
        ).fetchone() if has_receipts else None
        if receipt is not None:
            changed = str(receipt['authority_digest']) != authority_digest
            return DaySourceCoverage(source_changed=changed, repaired_complete=not changed)
        batches = connection.execute(
            'SELECT id,status,event_count FROM encoding_batches '
            'WHERE source_namespace=? AND active_date=?',
            (source_namespace, day.active_date),
        ).fetchall()
        if not batches:
            return DaySourceCoverage()
        settled = connection.execute(
            'SELECT 1 FROM day_settlements WHERE source_namespace=? AND active_date=? LIMIT 1',
            (source_namespace, day.active_date),
        ).fetchone() is not None
        complete = all(row['status'] == 'completed' for row in batches)
        zero = complete and all(row['event_count'] == 0 for row in batches)
        # An unfinished initial encode is allowed to resume its remaining
        # batches; its incomplete coverage alone is not a source mutation.
        if not settled and not zero:
            return DaySourceCoverage()
        rows = connection.execute(
            'SELECT s.* FROM encoding_batch_sources s JOIN encoding_batches b ON b.id=s.batch_id '
            'WHERE b.source_namespace=? AND b.active_date=? AND b.status=?',
            (source_namespace, day.active_date, 'completed'),
        ).fetchall()
    current = {ref.message_id: asdict(ref) for batch in day.batches for ref in batch.batch_sources}
    stored = {}
    for row in rows:
        values = dict(row)
        values.pop('batch_id')
        values.pop('source_order')
        identifier = values['message_id']
        if identifier in stored and stored[identifier] != values:
            return DaySourceCoverage(source_changed=True)
        stored[identifier] = values
    changed = stored != current
    if zero and not settled and changed:
        # A zero-event prefix has no durable day-settlement receipt. It can
        # equally be an interrupted encode or a formerly empty day followed
        # by a new tail; both safely resume ingestion. A mutation/removal of
        # an encoded row, or insertion inside its range, needs a new version.
        prefix = set(tuple(current)[:len(stored)])
        if stored and set(stored) == prefix and all(
            current[identifier] == values for identifier, values in stored.items()
        ):
            return DaySourceCoverage()
    return DaySourceCoverage(source_changed=changed, zero_event_complete=zero and not changed,
                             manifest_complete=complete and settled and not changed)
