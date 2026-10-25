"""Independent subjective, factual, interest and other-cognition lanes."""
import asyncio
import json
from datetime import datetime
from . import books, maintenance, autonomous, other_daily, interest_daily
from .maintenance_sources import event_source_coverage, sources, merge_evidence
from .daily_memory import recall
from llm_usage_scope import usage_scope
from .diagnostics import lane_attempt, emit
from .brief import defer_updates
from .daily_resume import completed_results, lane_receipt
from .habit_diagnostics import public_fields as habit_public_fields


class SourceReadError(books.BookError):
    """A required external evidence read failed before the other lane ran."""


async def _lane(step):
    """跑一路整理：异常收敛为返回值，CancelledError 照常上抛。"""
    try:
        return await step()
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        return exc


@defer_updates
async def run_daily(rows, llm, *, source_date, session_id, progress_cb=None, status_cb=None):
    def report(name, status, **metadata):
        if status_cb:
            status_cb(name, status, **metadata)
    report('preparation', 'running')
    try:
        evidence = sources(rows)
        source_coverage = event_source_coverage(rows, evidence)
    except Exception as exc:
        # Source preparation precedes the content-addressed lane receipt. Keep
        report('preparation', 'error')
        # a separate failure receipt, without copying source text or errors.
        rid = maintenance.digest({'pipeline': 'daily-v2-source-preparation',
                                  'date': source_date, 'session': session_id})
        now = datetime.now().astimezone().isoformat()
        state = {'id': rid, 'source_date': source_date, 'session_id': session_id,
                 'status': 'error', 'instance': maintenance._instance,
                 'started_at': now, 'completed_at': now,
                 'failure_stage': 'source_preparation',
                 'message': '认知原料准备失败；各通道尚未执行，保留原料等待重试。',
                 'lanes': dict.fromkeys(('world', 'self', 'interest', 'other'), 'not_started'),
                 'lane_errors': {'source_preparation': {'type': type(exc).__name__,
                                 'code': 'source_preparation_failed',
                                 'message': '认知原料准备失败'}}}
        books.atomic_text(maintenance.folder() / 'daily_runs' / (rid + '.json'),
                          json.dumps(state, ensure_ascii=False))
        raise SourceReadError('认知原料准备失败，请查看运行回执') from exc
    other_source_error = None
    other_evidence = evidence
    interest_source_error = None
    try:
        interest_evidence = interest_daily.evidence_for_day(source_date)
    except Exception as exc:
        interest_source_error = exc
        interest_evidence = []
    async def run_other():
        if other_source_error is not None:
            raise SourceReadError('他者书外部原料读取失败') from other_source_error
        return await other_daily.run_evidence(other_evidence, llm, source_date=source_date, session_id=session_id, progress_cb=progress_cb)
    async def run_interest():
        if interest_source_error is not None:
            raise SourceReadError('兴趣爱好原始材料读取失败') from interest_source_error
        return await interest_daily.run(source_date, session_id, llm, evidence=interest_evidence)
    rid = maintenance.digest({'pipeline':'daily-v2','date':source_date,'session':session_id,
                              'evidence':evidence, 'other_evidence':other_evidence,
                              'interest_evidence':interest_evidence})
    path = maintenance.folder() / 'daily_runs' / (rid+'.json')
    completed_lanes = completed_results(path, rid=rid, source_date=source_date, session_id=session_id)
    from .habit_natural_policy import for_daily as habit_policy_for_daily
    habit_policy = habit_policy_for_daily(path, source_date, session_id)
    # An unavailable required reader is not proof of unchanged empty material.
    if other_source_error is not None:
        completed_lanes.pop('other', None)
    if interest_source_error is not None:
        completed_lanes.pop('interest', None)
    state={'id':rid,'source_date':source_date,'session_id':session_id,'status':'running','instance':maintenance._instance,
           'habit_cycle_policy': habit_policy,
           'started_at':datetime.now().astimezone().isoformat(),
           'event_source_coverage':source_coverage, 'lane_results': dict(completed_lanes)}
    names = ('world', 'self', 'interest', 'other')
    state['lanes'] = {name: completed_lanes[name]['status'] if name in completed_lanes else 'pending'
                      for name in names}
    state['lanes']['brief'] = 'pending'
    books.atomic_text(path,json.dumps(state,ensure_ascii=False))
    memory=[]
    try:
        if any(name not in completed_lanes for name in ('world', 'self')):
            memory=await recall(evidence)
    except Exception:
        state['memory_status']='unavailable'
    report('preparation', 'completed')
    # 串行执行四路：并发的大请求曾把 Flash 挤到 504 超时。单路失败只影响自身。
    # 阶段状态只反映真实进展；统一分析不使用固定无进展时长告警。
    results=[]
    for name, step in zip(names, (
        lambda: maintenance.run(rows,llm,source_date=source_date,session_id=session_id,factual=True,memory=memory,
                               **({'habit_mode':habit_policy['mode'], 'habit_cycle_policy':habit_policy} if habit_policy else {})),
        lambda: autonomous.run(rows,llm,source_date=source_date,session_id=session_id,memory=memory,scope='self'),
        run_interest,
        run_other,
    )):
        state['lanes'][name] = 'running'
        books.atomic_text(path, json.dumps(state, ensure_ascii=False))
        report(name, 'running')
        try:
            with usage_scope("cognition_daily_" + name), lane_attempt(source_date, rid, name):
                if name in completed_lanes:
                    result = completed_lanes[name]
                    emit('lane_reused', result_status=result['status'])
                else:
                    result = await _lane(step)
                if isinstance(result, BaseException):
                    emit('lane_failed', error_code=autonomous.safe_failure_code(result))
                else:
                    emit('lane_finished', result_status=result.get('status'))
        except asyncio.CancelledError:
            report(name, 'interrupted')
            state['lanes'][name] = 'interrupted'
            state.update(status='interrupted', message='认知整理已中断；已保存结果保留，可续跑。',
                         items=[x for r in results if isinstance(r, dict) for x in r.get('items',[])],
                         completed_at=datetime.now().astimezone().isoformat())
            books.atomic_text(path, json.dumps(state, ensure_ascii=False))
            raise
        results.append(result)
        if isinstance(result, dict):
            state['lane_results'][name] = lane_receipt(result)
            if name == 'world':
                state['world_items'] = result.get('items', [])
                state.update(habit_public_fields(result))
        state['lanes'][name] = 'error' if isinstance(result, BaseException) else result.get('status')
        state['items'] = [x for r in results if isinstance(r, dict) for x in r.get('items',[])]
        books.atomic_text(path, json.dumps(state, ensure_ascii=False))
        report(name, state['lanes'][name], reused=name in completed_lanes)
        if progress_cb:
            progress_cb()
    errors=any(isinstance(r,BaseException) for r in results)
    partial=any(isinstance(r,dict) and r.get('status') == 'completed_with_rejections' for r in results)
    state.update(items=[x for r in results if isinstance(r,dict) for x in r.get('items',[])],
                 lanes={**{name: 'error' if isinstance(result,BaseException) else result.get('status') for name,result in zip(('world','self','interest','other'),results)}, 'brief': 'pending'})
    if errors: state['message']='部分整理未完成；已保存的内容保留，可重试。'
    elif partial: state['message']='可核实的候选已完成；少量无法逐条核实的候选已隔离。'
    if partial:
        state['lane_reference_diagnostics'] = {
            name: result['reference_review']['remaining_rejections']
            for name, result in zip(('world', 'self', 'interest', 'other'), results)
            if isinstance(result, dict) and result.get('reference_review', {}).get('remaining_rejections')}
        state['lane_rejections'] = {
            name: {'proposal_count': result.get('rejected_proposal_count', 0),
                   'review_count': result.get('discarded_review_count', 0),
                   **({'topic_annotation_count': result['rejected_topic_annotation_count']}
                      if result.get('rejected_topic_annotation_count') else {})}
            for name, result in zip(('world','self','interest','other'), results)
            if isinstance(result, dict) and result.get('status') == 'completed_with_rejections'}
    if errors:
        state['lane_errors'] = {name: {'type': type(result).__name__,
            'code': autonomous.safe_failure_code(result),
            'message': '外部原料读取失败' if isinstance(result, SourceReadError) else
                       '整理请求超时' if isinstance(result, TimeoutError) or getattr(result, 'status_code', None) == 504 else
                       '模型返回无法解析' if isinstance(result, json.JSONDecodeError) else
                       '资料或版本校验未通过' if isinstance(result, books.BookError) else '整理调用失败，保留材料等待重试'}
            for name, result in zip(('world','self','interest','other'), results) if isinstance(result, BaseException)}
        from .output_repair import ContractError, diagnostic
        for name, result in zip(names, results):
            if isinstance(result, ContractError):
                state['lane_errors'][name]['output_failure'] = diagnostic(result)
    from . import brief
    state['brief'] = {'status': 'running'}
    state['lanes']['brief'] = 'running'
    books.atomic_text(path,json.dumps(state,ensure_ascii=False))
    report('brief', 'running')
    try:
        state['brief'] = await brief.refresh(llm, source_date=source_date)
    except asyncio.CancelledError:
        report('brief', 'interrupted')
        state.update(status='interrupted', completed_at=datetime.now().astimezone().isoformat())
        state['brief'] = {'status': 'interrupted'}
        state['lanes']['brief'] = 'interrupted'
        books.atomic_text(path,json.dumps(state,ensure_ascii=False))
        raise
    except Exception as exc:
        state['brief'] = {'status': 'error', 'error_type': type(exc).__name__}
        from .brief_selection import SelectionCheckCoverageError
        if isinstance(exc, SelectionCheckCoverageError):
            state['brief'].update(failure_stage=exc.stage, failure_code=exc.code)
        from .output_repair import ContractError, diagnostic
        if isinstance(exc, ContractError):
            state['brief'].update(failure_stage=exc.stage, failure_code=exc.code,
                                  output_failure=diagnostic(exc))
    report('brief', state['brief'].get('status', 'completed'))
    state['lanes']['brief'] = state['brief'].get('status', 'completed')
    if state['brief'].get('status') == 'error':
        errors = True
        state['message'] = '认知摘要未完成；已保存的各路整理保留，可重试。'
        state.setdefault('lane_errors', {})['brief'] = {
            'type': state['brief'].get('error_type', 'BookError'),
            'code': state['brief'].get('failure_code', 'brief_refresh_failed'),
            'message': '认知摘要刷新失败',
            **({'output_failure': state['brief']['output_failure']} if state['brief'].get('output_failure') else {})}
    if state['brief'].get('status') == 'completed_with_rejections':
        partial = True
    state.update(status='error' if errors else 'completed_with_rejections' if partial else 'completed',
                 completed_at=datetime.now().astimezone().isoformat())
    books.atomic_text(path,json.dumps(state,ensure_ascii=False))
    if errors: raise books.BookError('部分认知整理失败，查看运行状态')
    return state
