"""Persistent cooperation identity, distinct from short AI model turns."""
import json
from .store import now

ACTIVE = {'waiting', 'thinking', 'reviewing'}


def load(service, identity):
    with service.store.db() as db:
        row = db.execute('SELECT result FROM commands WHERE key=?', ('residency:'+identity,)).fetchone()
    return json.loads(row[0]) if row else None


def save(service, record):
    record['updated_at'] = now()
    with service.store.db() as db:
        db.execute('INSERT OR REPLACE INTO commands VALUES(?,?)',
                   ('residency:'+record['id'], json.dumps(record,ensure_ascii=False)))


def records(service):
    with service.store.db() as db:
        rows = db.execute("SELECT result FROM commands WHERE key LIKE 'residency:%' ORDER BY rowid DESC").fetchall()
    return [json.loads(row[0]) for row in rows]


def attach(service, identity, invocation):
    old = load(service,identity)
    if old:
        return old
    existing = next((r for r in records(service) if r['state'] in ACTIVE),None)
    if existing:
        return existing
    record = {'id':identity, 'state':'waiting','phase':'opening','job_id':invocation.get('job_id') or '',
        'invocation':invocation,'segment':0,'actions':[],'action_failures':[],
        'experience':{'version':2,'received':[],'actions':[]},'created_at':now(),'updated_at':now(),
        'last_frontier':'','reason':'','finished_at':''}
    save(service,record)
    service.wake.set()
    return record


def status(service, identity=None):
    record = load(service,identity) if identity else next(iter(records(service)),None)
    if not record:
        return None
    job_id = record['job_id']
    if job_id:
        try:
            if service.store.job(job_id,include_deleted=True)['visibility']=='private':job_id=''
        except KeyError:job_id=''
    from body_admission import status as focus_status
    focus = focus_status()
    return {key:record.get(key) for key in ('id','state','phase','reason','created_at','updated_at','finished_at')} | {
        'job_id':job_id,'active':record['state'] in ACTIVE,
        'chat_available':not focus['busy'],
        'summary':{'opening':'正在准备这次合作','discussing':'AI 与工具手讨论中','making':'等待制作，合作现场保留',
            'checking':'等待作品检查，合作现场保留','reviewing':'AI 正在看成品','chatting':'先陪人类伙伴聊天，合作现场保留',
            'completed':'本次合作已完成','blocked':'合作已暂停，现场和工单已保留'}.get(record['phase'],'合作现场保留')}


def recover(service):
    for record in records(service):
        if record['state'] in ('thinking','reviewing'):
            from .residency import finish
            finish(service,record,'上次回应被中断，先核对实际工单再继续')


def automatic_delivery_allowed(service, job_id):
    if any(r['state'] in ACTIVE|{'blocked'} and r['job_id']==job_id for r in records(service)):
        return False
    return not kept_current(service,job_id)


def kept_current(service,job_id):
    with service.store.db() as db:
        row=db.execute("SELECT r.body FROM receipts r JOIN jobs j ON j.id=r.job_id WHERE r.job_id=? "
            "AND r.kind='completion_decided' AND json_extract(r.body,'$.revision')=j.revision ORDER BY r.seq DESC LIMIT 1",(job_id,)).fetchone()
    return bool(row and json.loads(row[0]).get('disposition') in ('keep','keep_private'))
