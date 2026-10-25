"""Private returned-body recovery; no new request and no erased failure receipt."""
import json
from contextlib import closing
from pathlib import Path

from .day_settlement_checkpoint import _digest, atomic_write


class RawPageCheckpoint:
    def __init__(self, db_path, page, request):
        self.identity = {'version': 'continuation-raw-v1', 'page_id': page.page_id,
            'input_digest': page.input_digest, 'prompt_version': page.prompt_version,
            'request_digest': _digest(request)}
        path = Path(db_path)
        self.path = path.with_name('.' + path.name + '.continuation-outputs') / (page.page_id + '.json')

    def start(self):
        if self.path.exists():
            raise ValueError('continuation_checkpoint_already_exists')
        atomic_write(self.path, {'identity': self.identity, 'status': 'started'})

    def returned(self, raw, usage=None):
        value = self.read()
        if value is None:
            raise ValueError('continuation_checkpoint_missing')
        if value.get('status') == 'returned':
            if value['raw_digest'] != _digest(raw):
                raise ValueError('continuation_checkpoint_conflicting_return')
            return
        atomic_write(self.path, {'identity': self.identity, 'status': 'returned',
            'raw_output': raw, 'raw_digest': _digest(raw), 'usage': usage or {},
            'usage_known': bool(usage)})

    def provider_output_error(self, exc):
        response = getattr(exc, 'response', None)
        if isinstance(response, dict) and isinstance(response.get('content'), str):
            atomic_write(self.path, {'identity': self.identity, 'status': 'provider_output_error',
                'raw_output': response['content'], 'raw_digest': _digest(response['content']),
                'finish_reason': response.get('finish_reason'), 'usage': getattr(exc, 'usage', {})})

    def read(self):
        if not self.path.exists():
            return None
        value = json.loads(self.path.read_text('utf8'))
        if value.get('identity') != self.identity:
            raise ValueError('continuation_checkpoint_identity_changed')
        if value.get('status') == 'returned' and value.get('raw_digest') != _digest(value.get('raw_output')):
            raise ValueError('continuation_checkpoint_body_changed')
        return value


def page_request(page):
    from .continuation_pages import page_messages
    from .day_settlement import day_settlement_response_schema
    schema = day_settlement_response_schema()['schema']['properties']['thread_judgments']
    return dict(messages=page_messages(page), schema_name='memory_v2_continuation_page',
        schema={'type': 'object', 'properties': {'thread_judgments': schema},
                'required': ['thread_judgments'], 'additionalProperties': False}, max_tokens=4096)


def prepared_request(model, page):
    request = page_request(page)
    prepare = getattr(model, 'prepare_continuation_request', None)
    return prepare(request) if prepare is not None else request


def restore_returned(store, settlement_id, model):
    """Revalidate saved bytes against current graph/source and actual source-bound input.

    Failed parsing remains a failure. A fixed parser can consume the original
    return later, while its old receipt remains immutable and the recovered
    validation is appended separately.
    """
    from .continuation_page_store import settlement_row, project_page, check_page, save_output
    from .continuation_pages import judgments_payload
    from .day_settlement import parse_continuation_judgments
    from .day_settlement_runner import normalize_thread_judgments
    from .store import _now_iso
    restored = 0
    with closing(store._connect()) as db:
        requests = db.execute(f"""SELECT r.* FROM continuation_page_requests r
            LEFT JOIN {output_relation(db)} o ON o.page_id=r.id
            LEFT JOIN continuation_page_blocks b ON b.page_id=r.id
            LEFT JOIN continuation_page_commits c ON c.page_id=r.id
            WHERE r.settlement_id=? AND (o.page_id IS NULL OR o.status!='validated')
              AND b.page_id IS NULL AND c.page_id IS NULL ORDER BY r.created_at""", (settlement_id,)).fetchall()
        for request in requests:
            path = Path(store.db_path)
            path = path.with_name('.' + path.name + '.continuation-outputs') / (request['id'] + '.json')
            if not path.exists():
                continue
            try:
                settlement = settlement_row(db, settlement_id)
                by_ref = {r['candidate_ref']: r for r in db.execute(
                    'SELECT * FROM day_continuation_candidates WHERE settlement_id=?', (settlement_id,))}
                page = project_page(db, settlement, [by_ref[r] for r in json.loads(request['candidate_refs_json'])],
                    page_id=request['id'], attempt_no=request['attempt_no'], prompt_version=request['prompt_version'])
                if page.input_digest != request['input_digest']:
                    continue
                checkpoint = RawPageCheckpoint(store.db_path, page, prepared_request(model, page))
                value = checkpoint.read()
                if value.get('status') != 'returned':
                    continue
                normalized, _ = normalize_thread_judgments(value['raw_output'], page.plan)
                output = judgments_payload(parse_continuation_judgments(page.plan, normalized))
                def commit(connection):
                    check_page(connection, page)
                    if prepared_request(model, page) != prepared_request_value:
                        raise ValueError('continuation_sources_changed_during_restore')
                    old = connection.execute('SELECT * FROM continuation_page_outputs WHERE page_id=?', (page.page_id,)).fetchone()
                    if old is None:
                        save_output(connection, page, 'validated', output, '', _now_iso())
                    if old is None or old['status'] != 'validated':
                        connection.execute("""CREATE TABLE IF NOT EXISTS continuation_page_recovered_outputs (
                            page_id TEXT PRIMARY KEY REFERENCES continuation_page_requests(id),
                            status TEXT NOT NULL CHECK(status='validated'), output_json TEXT NOT NULL,
                            error_code TEXT NOT NULL, created_at TEXT NOT NULL)""")
                        for action in ('UPDATE', 'DELETE'):
                            connection.execute(f"""CREATE TRIGGER IF NOT EXISTS recovered_page_no_{action.lower()}
                                BEFORE {action} ON continuation_page_recovered_outputs BEGIN
                                SELECT RAISE(ABORT, 'recovered page outputs are immutable'); END""")
                        previous = connection.execute('SELECT output_json FROM continuation_page_recovered_outputs WHERE page_id=?',
                            (page.page_id,)).fetchone()
                        body = json.dumps(output, ensure_ascii=False, sort_keys=True)
                        if previous is not None and previous[0] != body:
                            raise ValueError('continuation_recovered_output_conflict')
                        if previous is None:
                            connection.execute('INSERT INTO continuation_page_recovered_outputs VALUES (?,?,?,?,?)',
                                (page.page_id, 'validated', body, '', _now_iso()))

                prepared_request_value = prepared_request(model, page)
                if _digest(prepared_request_value) != checkpoint.identity['request_digest']:
                    continue
                store._continuation_transaction(commit)
                restored += 1
            except (ValueError, KeyError, TypeError, OSError):
                # Existing unknown/error remains visible and still cannot send again.
                continue
    return restored


def returned_dates(connection, source_namespace, before_active_date):
    """Read-only nominations, not proof of valid output and never a paid grant."""
    db_path = next((r[2] for r in connection.execute('PRAGMA database_list') if r[1] == 'main'), '')
    if not db_path:
        return ()
    path = Path(db_path)
    folder = path.with_name('.' + path.name + '.continuation-outputs')
    if not folder.is_dir():
        return ()
    rows = connection.execute(f"""SELECT s.active_date,r.id,r.input_digest FROM day_settlements s
        JOIN continuation_page_requests r ON r.settlement_id=s.id
        LEFT JOIN {output_relation(connection)} o ON o.page_id=r.id
        LEFT JOIN continuation_page_commits c ON c.page_id=r.id
        LEFT JOIN continuation_page_blocks b ON b.page_id=r.id
        WHERE s.source_namespace=? AND s.active_date<? AND c.page_id IS NULL AND b.page_id IS NULL
          AND (o.page_id IS NULL OR o.status!='validated')""", (source_namespace, before_active_date))
    dates = set()
    for row in rows:
        try:
            value = json.loads((folder / (row['id'] + '.json')).read_text('utf8'))
            if (value.get('status') == 'returned' and value.get('raw_digest') == _digest(value.get('raw_output'))
                    and value.get('identity', {}).get('page_id') == row['id']
                    and value['identity'].get('input_digest') == row['input_digest']):
                dates.add(row['active_date'])
        except (OSError, ValueError, TypeError):
            continue
    return tuple(sorted(dates))


def output_relation(connection):
    """Effective validated replay view; the original failed output remains immutable."""
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='continuation_page_recovered_outputs'").fetchone():
        return '(SELECT o.* FROM continuation_page_outputs o WHERE NOT EXISTS (SELECT 1 FROM continuation_page_recovered_outputs r WHERE r.page_id=o.page_id) UNION ALL SELECT * FROM continuation_page_recovered_outputs)'
    return 'continuation_page_outputs'
