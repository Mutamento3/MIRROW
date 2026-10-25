"""Explicit, backed-up retry of one world's active day, not other lanes."""
from backup_service import operation_artifacts
import asyncio
import json
import shutil
from datetime import datetime
from uuid import uuid4
from . import books, maintenance
from .maintenance_sources import sources
from .world_evolution import PIPELINE

_retry_lock = asyncio.Lock()


async def retry(source_date, session_id, rows, llm):
    async with _retry_lock:
        return await _retry(source_date, session_id, rows, llm)


async def _retry(source_date, session_id, rows, llm):
    datetime.strptime(source_date, '%Y-%m-%d')
    evidence = sources(rows)
    if not evidence: raise books.BookError('这一天没有可用私聊材料')
    identity = maintenance.digest({'date': source_date, 'session': session_id,
                                   'messages': evidence, 'pipeline': PIPELINE})
    request_path = maintenance.folder() / 'world_retry_requests' / (identity + '.json')
    request = json.loads(request_path.read_text('utf-8')) if request_path.exists() else {}
    # A completed world lane may still have an unfinished receipt repair.
    # Only success of this entire wrapper authorizes the next explicit rerun.
    resumable = bool(request.get('generation')) and request.get('completed') is not True
    generation = request['generation'] if resumable else uuid4().hex
    run_id = maintenance.digest({'date':source_date,'session':session_id,'messages':evidence,
                                 'pipeline':PIPELINE,'generation':generation})
    request_record = {'identity': identity, 'generation': generation,
        'run_id': run_id, 'source_date': source_date, 'session_id': session_id, 'completed': False}
    books.atomic_text(request_path, json.dumps(request_record, ensure_ascii=False))
    with operation_artifacts(books.ROOT / 'backups', 'world_retry') as target:
        manifest = []
        with books.LOCK:
            paths = [*books.folder('world').rglob('*.md'), *maintenance.folder().rglob('*.json')]
            for path in paths:
                relative = path.relative_to(books.ROOT)
                dest = target / 'originals' / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
                if dest.read_bytes() != path.read_bytes(): raise books.BookError('备份期间材料变化，停止重试')
                manifest.append({'path':relative.as_posix(),'revision':books.revision(path.read_text('utf-8'))})
            books.atomic_text(target/'manifest.json',json.dumps(manifest,ensure_ascii=False))
        calls = 0
        async def traced(prompt):
            nonlocal calls
            calls += 1
            result = await llm(prompt)
            output = result if isinstance(result,str) else json.dumps(result,ensure_ascii=False)
            books.atomic_text(target/f'call_{calls}_usage.json',json.dumps({
                'input_hash':books.revision(prompt),'input_chars':len(prompt),
                **(prompt.transport_metadata() if hasattr(prompt, 'transport_metadata') else {}),
                'output_hash':books.revision(output),'output_chars':len(output)},ensure_ascii=False))
            return result
        try:
            from .daily_memory import recall
            try: memory = await recall(evidence)
            except Exception: memory = []
            result = await maintenance.run(rows,traced,source_date=source_date,session_id=session_id,
                                           factual=True,memory=memory,generation=generation)
            # Current receipts identify the session explicitly. For old receipts,
            # verify attribution through their item references or the exact v1 key;
            # never overwrite another session just because its date is equal.
            legacy_id = maintenance.digest({'pipeline':'daily-v1','date':source_date,'session':session_id,'evidence':evidence})
            for path in (maintenance.folder()/'daily_runs').glob('*.json'):
                state=json.loads(path.read_text('utf-8'))
                if state.get('source_date') != source_date:
                    continue
                sessions = {maintenance.get(i).get('session_id') for i in state.get('items', [])
                            if maintenance.record_path(i).exists()}
                if not (state.get('session_id') == session_id or path.stem == legacy_id
                        or (not state.get('session_id') and sessions == {session_id})):
                    continue
                state.setdefault('lanes',{})['world']=result['status']
                state.get('lane_errors',{}).pop('world',None)
                state['items']=list(dict.fromkeys([*state.get('items',[]),*result.get('items',[])]))
                state['status']='error' if any(v in {'error','running','interrupted'} for v in state['lanes'].values()) else \
                    'completed_with_rejections' if any(v == 'completed_with_rejections' for v in state['lanes'].values()) else 'completed'
                state['world_retried_at']=datetime.now().astimezone().isoformat()
                books.atomic_text(path,json.dumps(state,ensure_ascii=False))
            counts={}
            for iid in result.get('items',[]):
                item=maintenance.get(iid); counts[item['status']]=counts.get(item['status'],0)+1
            summary={'status':result['status'],'counts':counts,'calls':calls,'backup':target.name,
                     'run_id':run_id,'source_date':source_date,'resumed':resumable}
        except Exception as exc:
            path=maintenance.folder()/'runs'/(run_id+'.json')
            state=json.loads(path.read_text('utf-8')) if path.exists() else {}
            summary={'status':'error','error_type':type(exc).__name__,'phase':state.get('phase'),
                     'detail':state.get('error_detail','重试失败'),'proposal_index':state.get('proposal_index'),'calls':calls,'backup':target.name}
            summary.update(run_id=run_id, source_date=source_date, resumed=resumable)
        books.atomic_text(target/'result.json',json.dumps(summary,ensure_ascii=False))
        if summary['status'] in {'completed', 'completed_with_rejections'}:
            books.atomic_text(request_path, json.dumps({**request_record, 'completed': True}, ensure_ascii=False))
        return summary
