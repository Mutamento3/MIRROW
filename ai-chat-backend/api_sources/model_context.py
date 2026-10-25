"""Read immutable message receipts; project model names without provider details."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from model_provenance import normalize_model_label, current_call_message, day_model_evidence
from .audit import DB_PATH


def labels_for_ids(message_ids, *, path=DB_PATH):
    ids = list(dict.fromkeys(str(value) for value in message_ids if value))
    if not ids or not Path(path).exists():
        return {}
    result = {}
    with closing(sqlite3.connect(f'file:{Path(path).as_posix()}?mode=ro', uri=True, timeout=3)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='message_sources'").fetchone():
            return {}
        for start in range(0, len(ids), 400):
            chunk = ids[start:start+400]
            rows = db.execute('SELECT message_id,source_json FROM message_sources WHERE message_id IN ('
                              + ','.join('?' for _ in chunk) + ')', chunk)
            for mid, raw in rows:
                value = json.loads(raw)
                label = normalize_model_label(value.get('model'))
                if label:
                    result[mid] = label
    return result


def enrich_rows(rows, *, path=DB_PATH):
    rows = [dict(row) if isinstance(row, dict) else dict(vars(row)) for row in rows]
    labels = labels_for_ids((r.get('message_id') or r.get('id') for r in rows), path=path)
    for row in rows:
        # Never trust a browser-supplied modelSource as a provenance authority.
        if row.get('role') == 'assistant':
            label = labels.get(str(row.get('message_id') or row.get('id') or ''))
            row.pop('generation_model', None)
            if label:
                row['generation_model'] = label
    return rows


def day_evidence(rows, *, path=DB_PATH):
    return day_model_evidence(enrich_rows(rows, path=path))


def with_current_model(messages, model):
    # Owned wire copy: no mutation of original history or earlier call markers.
    result = [deepcopy(m) for m in messages if m.get('name') != 'mirrow_current_model']
    result.append(current_call_message(model))
    return result
