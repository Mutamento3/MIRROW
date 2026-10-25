"""Fair maintenance ordering derived from existing persisted page requests."""
from contextlib import closing
from pathlib import Path
import sqlite3


def ordered_dates(db_path, dates, *, source_namespace):
    """Saved outputs first, then least recently attempted; validation stays in the loader."""
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        last = dict(db.execute('''
            SELECT d.active_date, MAX(p.created_at)
            FROM day_settlements d JOIN continuation_page_requests p ON p.settlement_id = d.id
            WHERE d.source_namespace = ? GROUP BY d.active_date
        ''', (source_namespace,)))
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        saved = set()
        if {'continuation_page_outputs', 'continuation_page_commits', 'continuation_page_blocks'} <= tables:
            from .continuation_raw_checkpoint import output_relation
            candidates = db.execute(f'''
                SELECT DISTINCT d.active_date,d.id FROM day_settlements d
                JOIN continuation_page_requests p ON p.settlement_id=d.id
                JOIN {output_relation(db)} o ON o.page_id=p.id
                LEFT JOIN continuation_page_commits c ON c.page_id=p.id
                LEFT JOIN continuation_page_blocks b ON b.page_id=p.id
                WHERE d.source_namespace=? AND o.status='validated'
                  AND c.page_id IS NULL AND b.page_id IS NULL
            ''', (source_namespace,)).fetchall()
            from .continuation_page_store import load_page
            for row in candidates:
                if row['active_date'] not in dates:
                    continue
                try:
                    page, output, _, _ = load_page(db, row['id'], False)
                except ValueError:
                    continue
                if page is not None and output is not None:
                    saved.add(row['active_date'])
    # This is scheduling priority only: source/revision/withdrawal validation
    # remains mandatory in load_page before any output can be committed.
    return tuple(sorted(set(dates), key=lambda day: (day not in saved, last.get(day, ''), day)))
