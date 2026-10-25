"""Explicit owner setting suspends budget enforcement during chain tests."""


def settle(store):
    if store.settings()['budget_limits_enabled']:
        return
    for job in store.jobs():
        if job['state']!='budget_paused':
            continue
        with store.db() as db:
            changed=db.execute("UPDATE jobs SET state='queued',reason='' WHERE id=? AND state='budget_paused'",(job['id'],)).rowcount
            if changed:
                store._event(db,job['id'],'budget_policy',{'revision':job['revision'],'limits_enabled':False,
                    'summary':'人类伙伴暂时关闭预算限制以验证链路；保留方案确认、累计用量与桌面空闲准入，已恢复排队。'})
    with store.db() as db:
        db.execute("UPDATE quality_reviews SET status='queued',reason='' WHERE status='budget_paused'")
