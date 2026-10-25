"""Durable drafts and compare-and-set publication claims; never replay submit."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager, closing
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def now():
    return datetime.now().astimezone().isoformat()

def validate_draft(value):
    if not isinstance(value,dict) or set(value)-{'title','body','cover_text'}:
        raise ValueError('invalid_draft_fields')
    result={k:value.get(k,'') for k in ('title','body','cover_text')}
    if any(not isinstance(x,str) for x in result.values()):
        raise ValueError('invalid_draft_text')
    if not result['title'].strip() or len(result['title'].encode('utf-16-le'))//2>20:
        raise ValueError('title_requires_1_to_20_units')
    if not result['body'].strip() or len(result['body'].encode('utf-16-le'))//2>1000:
        raise ValueError('body_requires_1_to_1000_units')
    if not result['cover_text'].strip() or len(result['cover_text'])>180:
        raise ValueError('cover_requires_1_to_180_characters')
    if any('\x00' in x for x in result.values()):
        raise ValueError('invalid_text_control')
    return result

class PublishStore:
    def __init__(self,path=None):
        self.path=Path(path) if path else ROOT/'events'/'xhs_publish.db'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS publications (task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, source_key TEXT NOT NULL, state TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(session_id,source_key))')
            db.execute('CREATE TABLE IF NOT EXISTS publication_claims (fingerprint TEXT PRIMARY KEY,task_id TEXT NOT NULL)')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()
    def get(self,task_id,session_id):
        with self.connect() as db:
            row=db.execute('SELECT record FROM publications WHERE task_id=? AND session_id=?',(task_id,session_id)).fetchone()
        return json.loads(row[0]) if row else None
    def create(self,session_id,source_key,draft,account):
        if not session_id or not source_key:
            raise ValueError('publication_identity_required')
        draft=validate_draft(draft)
        task_id=hashlib.sha256((session_id+'\n'+source_key).encode()).hexdigest()
        record={'task_id':task_id,'session_id':session_id,'state':'draft','draft':draft,
            'account':account,'created_at':now(),'updated_at':now(),'receipt':{},'events':[]}
        with self.connect() as db:
            claim=db.execute('SELECT task_id FROM publication_claims WHERE fingerprint=?',(fingerprint(record),)).fetchone()
            if claim:
                old=self.get(claim[0],session_id)
                if old: return old
                raise ValueError('identical_publication_already_claimed')
            db.execute('INSERT OR IGNORE INTO publications VALUES (?,?,?,?,?)',
                (task_id,session_id,source_key,'draft',json.dumps(record,ensure_ascii=False)))
        saved=self.get(task_id,session_id)
        if saved['draft']!=draft or saved['account']!=account:
            raise ValueError('source_identity_already_bound')
        return saved
    def transition(self,task_id,session_id,expected,state,**updates):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT record FROM publications WHERE task_id=? AND session_id=?',(task_id,session_id)).fetchone()
            if not row: raise ValueError('publication_not_found')
            item=json.loads(row[0])
            if item['state'] not in expected: raise ValueError('publication_state_conflict')
            if state=='submitting':
                identity=fingerprint(item)
                db.execute('INSERT OR IGNORE INTO publication_claims VALUES (?,?)',(identity,task_id))
                claim=db.execute('SELECT task_id FROM publication_claims WHERE fingerprint=?',(identity,)).fetchone()
                if claim[0]!=task_id: raise ValueError('identical_publication_already_claimed')
            item.update(updates,state=state,updated_at=now())
            item['events'].append({'state':state,'at':item['updated_at']})
            db.execute('UPDATE publications SET state=?,record=? WHERE task_id=?',
                (state,json.dumps(item,ensure_ascii=False),task_id))
        return item

def fingerprint(record):
    # Changing source turn, cover, or entry channel cannot repeat an unknown submit.
    material=[record['account']['account_id'],record['draft']['title'],record['draft']['body']]
    return hashlib.sha256(json.dumps(material,ensure_ascii=False).encode()).hexdigest()

def account_config():
    path=ROOT/'events'/'xhs_publish_config.json'
    if not path.exists(): raise ValueError('publish_account_not_configured')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not value.get('account_id') or not value.get('account_name'):
        raise ValueError('publish_account_not_configured')
    return {k:str(value[k]) for k in ('account_id','account_name')}

def autonomy_available():
    """This rollout's first post is invited in chat, as requested by Human."""
    account=account_config()
    path=ROOT/'events'/'xhs_publish.db'
    if not path.exists(): return False
    with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)) as db:
        rows=db.execute("SELECT record FROM publications WHERE state='published' AND source_key LIKE 'chat:%'").fetchall()
    return any(json.loads(row[0]).get('account')==account for row in rows)

def receipt(record):
    return {'task_id':record['task_id'],'status':record['state'],'account':record['account'],
        'title':record['draft']['title'],'body':record['draft']['body'],
        'cover_text':record['draft']['cover_text'],'media_kind':'native_text_card',
        'receipt':record.get('receipt',{}),'error':record.get('error','')}
