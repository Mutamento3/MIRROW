"""Read-only progress of one settled day; no scheduling or model calls."""
from contextlib import closing
from pathlib import Path
import sqlite3


def read_progress(db_path, *, source_namespace='mainline'):
    from .continuation_pages import pending_rows, tables_available
    from .continuation_page_store import load_page
    from .day_settlement import DAY_SETTLEMENT_VERSION
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        if not tables_available(connection):
            return {'status': 'unavailable'}
        # A newer compact-only operator revision has no continuation inventory.
        # Read the receipt maintained by the continuation runner, not that view.
        row = connection.execute('SELECT id,active_date FROM day_settlements WHERE source_namespace=? '
                                 'AND settlement_version=? '
                                 'ORDER BY active_date DESC,created_at DESC LIMIT 1',
                                 (source_namespace, DAY_SETTLEMENT_VERSION)).fetchone()
        if row is None:
            return {'status': 'not_started'}
        result = {'active_date': row['active_date'], 'remaining_count': len(pending_rows(connection, row['id'])),
                  'committed_page_count': connection.execute('SELECT COUNT(*) FROM continuation_page_commits c '
                      'JOIN continuation_page_requests r ON r.id=c.page_id WHERE r.settlement_id=?', (row['id'],)).fetchone()[0]}
        try:
            page, saved, _, attention = load_page(connection, row['id'], False)
        except ValueError:
            return {**result, 'status': 'needs_replan'}
        return {**result, 'status': 'attention_required' if attention else 'pending' if page else 'complete',
                'next_page_candidate_count': len(page.receipts) if page else 0,
                'has_saved_validated_output': saved is not None}


def configured_progress():
    try:
        from .online_ingest import configuration_from_environment
        configuration = configuration_from_environment()
        if configuration is None:
            return {'status': 'disabled'}
        return read_progress(configuration.memory_db_path)
    except (OSError, ValueError, sqlite3.Error):
        return {'status': 'unavailable'}
