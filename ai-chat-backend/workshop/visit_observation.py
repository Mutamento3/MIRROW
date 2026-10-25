"""State comparison facts; this never decides whether AI may visit."""
import hashlib,json
from .store import now


def frontier(service):
    state=service.snapshot()
    jobs=[{key:job[key] for key in ('id','revision','state','plan_version','confirmed_version','reason')}
          for job in service.store.jobs()]
    with service.store.db() as db:
        records={table:[tuple(row) for row in db.execute(query)] for table,query in {
            'messages':'SELECT job_id,max(seq) FROM messages GROUP BY job_id ORDER BY job_id',
            'artifacts':'SELECT id,job_id,revision,sha256 FROM artifacts ORDER BY id',
            'quality':'SELECT artifact_id,status FROM quality_reviews WHERE seq IN (SELECT max(seq) FROM quality_reviews GROUP BY artifact_id) ORDER BY artifact_id',
            'cabinet_visits':'SELECT id,created_at FROM cabinet_visits ORDER BY id',
            'cabinet_views':'SELECT visit_id,artifact_id,created_at FROM cabinet_views ORDER BY visit_id,artifact_id',
        }.items()}
    remaining=state['quota']['remaining']
    quota_ready=remaining is not None and remaining>=state['settings']['remaining_floor']
    execution={'account_ready':quota_ready,'dispatch_enabled':state['settings']['enabled']}
    value={'jobs':jobs,'records':records,'execution':execution}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def capture(service):
    return {'observed_at':now(),'frontier':frontier(service),'comparison_version':3}


def since_last_visit(service):
    with service.store.db() as db:
        rows=db.execute("SELECT result FROM commands WHERE key LIKE 'visit:%' ORDER BY rowid DESC").fetchall()
    previous=next((value for row in rows if (value:=json.loads(row[0])).get('observation')),None)
    if previous is None:
        return {'source':'已保存的实际工作坊来访回执','last_visit_at':None,'changed_since_last_visit':None}
    observation=previous['observation']
    return {'source':'实际来访结束时保存的需求、对话、作品、质检与账号准入比较；查看回执、周期查询和桌面忙闲不属于合作进展',
            'last_visit_at':observation['observed_at'],
            'last_actions':[{key:row[key] for key in ('action','job_id','revision','artifact_id','execution_status','reason','browser_status') if key in row}
                            for row in (previous.get('experience') or {}).get('actions', [])][-8:],
            'action_failures':previous.get('action_failures', []),
            'changed_since_last_visit':observation['frontier']!=frontier(service) if observation.get('comparison_version')==3 else None}
