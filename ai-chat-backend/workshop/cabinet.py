"""Human's explicit cabinet entries and visible works; reads never count visits."""
import json

from .store import now


def install(store):
    with store.db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS cabinet_visits (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cabinet_views (
                visit_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
                created_at TEXT NOT NULL, accepted_seq INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(visit_id,artifact_id));
        ''')
        if 'accepted_seq' not in {row['name'] for row in db.execute('PRAGMA table_info(cabinet_views)')}:
            db.execute('ALTER TABLE cabinet_views ADD COLUMN accepted_seq INTEGER NOT NULL DEFAULT 0')


def summary(store):
    with store.db() as db:
        row = db.execute('SELECT count(*) AS visit_count,max(created_at) AS last_visited_at FROM cabinet_visits').fetchone()
        unread = db.execute("SELECT count(*) FROM receipts WHERE kind='cabinet_visited' AND consumed=0").fetchone()[0]
    return {**dict(row), 'unread_visits': unread, 'source': '人类伙伴主动进入作品柜的已保存记录；浏览次数不表示喜欢或验收'}


def view_stats(db, artifact_id):
    return dict(db.execute('SELECT count(*) AS view_count,max(created_at) AS last_viewed_at '
                          'FROM cabinet_views WHERE artifact_id=?', (artifact_id,)).fetchone())


def archived_ids(store):
    """Completed rooms belong to history; Human's visits are a separate fact."""
    with store.db() as db:
        return {row['id'] for row in db.execute("SELECT id FROM jobs WHERE deleted=0 AND state='accepted'")}


def record_visit(store, request_id, artifact_ids=None):
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        raise ValueError('invalid_visit_id')
    if artifact_ids is None:
        artifact_ids = []
    if not isinstance(artifact_ids, list) or len(artifact_ids) > 50 or any(not isinstance(v, str) for v in artifact_ids):
        raise ValueError('invalid_artifact_ids')
    with store.db() as db:
        # Validate before creating an entry: human UI has no access to private works.
        for artifact_id in set(artifact_ids):
            if not db.execute("SELECT 1 FROM artifacts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND j.deleted=0 AND j.visibility='public'", (artifact_id,)).fetchone():
                raise KeyError('work_not_available')
        timestamp = now()
        inserted = db.execute('INSERT OR IGNORE INTO cabinet_visits VALUES(?,?)', (request_id,timestamp)).rowcount
        if inserted:
            count = db.execute('SELECT count(*) FROM cabinet_visits').fetchone()[0]
            store._event(db, '', 'cabinet_visited', {'source':'human', 'visit_id':request_id,
                'visit_count':count, 'visited_at':timestamp,
                'summary':f'人类伙伴来参观作品柜了，这是第 {count} 次；参观时间 {timestamp}。浏览记录不代表验收或喜欢。'})
        for artifact_id in set(artifact_ids):
            receipt = db.execute("SELECT r.seq,r.body,j.state FROM receipts r JOIN jobs j ON j.id=r.job_id "
                "JOIN artifacts a ON a.job_id=j.id WHERE a.id=? AND r.kind='accepted_work' ORDER BY r.seq DESC LIMIT 1", (artifact_id,)).fetchone()
            accepted_seq = receipt['seq'] if receipt and receipt['state']=='accepted' and json.loads(receipt['body']).get('artifact_id')==artifact_id else 0
            db.execute('INSERT OR IGNORE INTO cabinet_views(visit_id,artifact_id,created_at,accepted_seq) VALUES(?,?,?,?)',
                       (request_id,artifact_id,timestamp,accepted_seq))
    return {'visit_id':request_id, 'new_visit':bool(inserted), **summary(store)}


def private_archives(store, offset=0):
    """Human-visible archive markers, independent of artifacts and AI's active queue."""
    offset = max(0, int(offset))
    with store.db() as db:
        where = "deleted=0 AND state='accepted' AND visibility='private'"
        total = db.execute('SELECT count(*) FROM jobs WHERE '+where).fetchone()[0]
        rows = [dict(row) for row in db.execute(
            'SELECT id,created_at,updated_at,'
            "(SELECT max(created_at) FROM receipts r WHERE r.job_id=jobs.id AND r.kind='accepted_work') AS accepted_at "
            'FROM jobs WHERE '+where+' ORDER BY updated_at DESC,id DESC LIMIT 50 OFFSET ?', (offset,))]
    return {'jobs':[{**row, 'state':'accepted', 'visibility':'private'} for row in rows],
            'total':total, 'offset':offset, 'has_more':offset+len(rows)<total}


def notification(receipt):
    return {'receipt_id':receipt['id'], 'job_id':'', 'title':'作品柜', 'visibility':'public',
            'kind':receipt['kind'], 'timestamp':receipt['created_at'], **receipt['body']}
