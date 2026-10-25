"""Recover official cumulative receipts for an interrupted workshop thread."""
import json
import sqlite3
from pathlib import Path


def reconcile(store, job_id):
    job=store.job(job_id)
    if not job['thread_id'] or not job['turn_id']:
        return False
    home=(store.directory/'codex-home').resolve()
    try:
        with sqlite3.connect((home/'state_5.sqlite').as_uri()+'?mode=ro',uri=True,timeout=.5) as db:
            row=db.execute('SELECT rollout_path FROM threads WHERE id=?',(job['thread_id'],)).fetchone()
        if not row:
            return False
        path=Path(row[0]).resolve()
        if not path.is_relative_to(home) or path.stat().st_size>64*1024*1024:
            return False
        identified=False; active=False; terminal=False; output_seen=False; total=None; fresh=False
        with path.open(encoding='utf8') as stream:
            for line in stream:
                if len(line)>8*1024*1024:
                    return False
                entry=json.loads(line); body=entry.get('payload') or {}
                if entry.get('type')=='session_meta':
                    identified=body.get('id')==job['thread_id']
                if entry.get('type')=='response_item' and active and (
                        body.get('role')=='assistant' or body.get('type') in ('function_call','custom_tool_call')):
                    output_seen=True
                if entry.get('type')!='event_msg':
                    continue
                kind=body.get('type')
                if kind=='task_started':
                    active=body.get('turn_id')==job['turn_id']
                    if active: terminal=False;output_seen=False;fresh=False
                if kind=='token_count':
                    value=((body.get('info') or {}).get('total_token_usage') or {}).get('total_tokens')
                    if type(value) is int and value>=0:
                        total=value
                        if active:fresh=True
                if active and kind=='turn_aborted' and body.get('turn_id')==job['turn_id']:
                    terminal=True
        # Without a new count, only an aborted pre-response turn can keep the
        # previous official watermark. Future cumulative increments still debit it.
        if not identified or not active or not terminal or total is None or (not fresh and output_seen):
            return False
        if not store.record_usage(job_id,job['thread_id'],total):
            return False
        store.event(job_id,'usage_reconciled',{'revision':job['revision'],'source':'Codex 本地官方累计 token_count 回执',
            'cumulative_tokens':store.job(job_id)['tokens'],'fresh_turn_usage':fresh,
            'summary':'中断现场已核对，保留官方累计用量水位；后续官方增量继续累计，不追加额度。'})
        return True
    except (OSError,ValueError,TypeError,sqlite3.Error):
        return False


def settle(service):
    for job in service.store.jobs():
        if job['state']!='budget_paused' or job['reason']!='usage_unknown':
            continue
        if not reconcile(service.store,job['id']):
            continue
        current=service.store.job(job['id'])
        if current['state']!='budget_paused' or current['reason']!='usage_unknown':
            continue
        if current['tokens']>=current['token_cap']:
            service.store.update(job['id'],reason='token_budget')
            continue
        with service.store.db() as db:
            db.execute("UPDATE jobs SET state='queued',reason='' WHERE id=? AND state='budget_paused' AND reason='usage_unknown'",(job['id'],))
