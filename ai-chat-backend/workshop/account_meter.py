"""Observed account-window decreases only within workshop execution segments."""
import json
import math
import time
from .protocol import quota_bucket
from .budget import same_window


def disable(store):
    """Close every open observation boundary without charging parallel work."""
    with store.db() as db:
        for row in db.execute("SELECT key,result FROM commands WHERE key LIKE 'account_meter:%'").fetchall():
            record=json.loads(row['result'])
            record.update(active=False,last={},status='disabled')
            db.execute('UPDATE commands SET result=? WHERE key=?',(json.dumps(record),row['key']))


def sample(store,job_id,model,payload,*,active=True,new_segment=False,aggregate=False):
    if store.settings()['max_parallel'] > 1 and not aggregate:
        return
    from .account_activity import external_active
    if external_active():
        disable(store)
        return
    if not store.settings()['desktop_idle_only']:
        disable(store)
        return
    bucket,limits=quota_bucket(payload,model)
    windows={}
    for name in ('primary','secondary'):
        value=limits.get(name) or {}
        used=value.get('usedPercent');minutes=value.get('windowDurationMins');reset=value.get('resetsAt')
        if type(used) in (float,int) and math.isfinite(used) and 0<=used<=100 and type(minutes) is int and minutes>0 and type(reset) is int:
            windows[name]={'bucket':bucket,'name':name,'used':used,'window_mins':minutes,'resets_at':reset}
    key='account_meter:'+job_id
    with store.db() as db:
        row=db.execute('SELECT result FROM commands WHERE key=?',(key,)).fetchone()
        record=json.loads(row[0]) if row else {'totals':{},'segments':0,'active':False,'last':{}}
        from .account_activity import external_epoch
        if record.get('external_epoch', 0) != external_epoch():
            record.update(active=False,last={})
        record['external_epoch'] = external_epoch()
        if new_segment:
            record.update(active=False,last={})
        if windows and not record.get('display_window'):
            record['display_window']=max(windows,key=lambda name:windows[name]['used'])
        if active and not record['active']:record['segments']+=1
        for name,current in windows.items():
            previous=record['last'].get(name) if record['active'] else None
            if previous and same_window(previous,current) and current['used']>=previous['used']:
                record['totals'][name]=round(record['totals'].get(name,0)+current['used']-previous['used'],4)
            else:record['totals'].setdefault(name,0)
        record.update(active=active,last=windows if active else {},checked_at=time.time(),
                      window_mins={name:row['window_mins'] for name,row in windows.items()},
                      status='observing' if windows else 'receipt_unavailable')
        db.execute('INSERT OR REPLACE INTO commands VALUES(?,?)',(key,json.dumps(record)))


def summary(store,job_id,*,aggregate=False):
    from .account_activity import external_active
    if external_active():
        return {'status':'parallel_native_work','observed_percent':None,'active':False,
                'note':'其他任务正在使用同一账号；工单额度归因暂停，不把它算成工作坊消耗。'}
    if not store.settings()['desktop_idle_only']:
        return {'status':'disabled','observed_percent':None,'active':False,
                'note':'直接开工模式：不统计账号额度下降，避免混入桌面任务；原有统计记录保留。工单与质检的实际 token 回执仍记录。'}
    if store.settings()['max_parallel'] > 1 and not aggregate:
        return {'status':'parallel_workshop','observed_percent':None,'active':False,
                'note':'并行模式：账号下降统一记录在工作坊，每单保存自己的实际 token 回执。'}
    with store.db() as db:row=db.execute('SELECT result FROM commands WHERE key=?',('account_meter:'+job_id,)).fetchone()
    if not row:return {'status':'waiting_sample','observed_percent':None,'note':'仅统计实际开工段内观察到的账号额度下降，暂停期间不计入；官方回执延迟可能影响边界。'}
    record=json.loads(row[0]);name=record.get('display_window') or ('primary' if 'primary' in record['totals'] else 'secondary')
    return {'status':record['status'],'observed_percent':record['totals'].get(name),'window_mins':record['window_mins'].get(name),
            'active':record['active'],'segments':record['segments'],'checked_at':record['checked_at'],
            'window_percent':record['totals'],'note':'开工期间的账号额度下降观测值；暂停段排除。不同额度窗口分别记录，不叠加；官方回执可能延迟。'}


async def observe(runner,store,job_id,model,*,active=True,new_segment=False):
    if store.settings()['max_parallel'] > 1 or getattr(runner, '_workshop_parallel_meter', False):
        return
    if not store.settings()['desktop_idle_only']:
        disable(store)
        return
    try:payload=await runner.request('account/rateLimits/read',{},timeout=6)
    except Exception:payload={}
    sample(store,job_id,model,payload,active=active,new_segment=new_segment)
