"""Durable, content-free day-end failures and verified acknowledgement."""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
import threading
import uuid
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parent
LOCK = threading.RLock()
INSTANCE = uuid.uuid4().hex
LABELS = {'self': '自我书', 'interest': '兴趣爱好', 'world': '世界书', 'other': '他者书', 'brief': '认知摘要', 'source_preparation': '认知原料准备', 'memory_v2': 'Memory V2', 'diary_generation': '日记生成', 'diary_backfill': '日记补全', 'daily_aggregation': '身体聚合', 'social_feed_summary': '朋友圈收尾', 'unified_analysis': '认知整理'}
REASONS = {'invalid_references': '候选的来源引用缺失或无法定位；需要复核引用。', 'output_truncated': '模型输出达到长度上限，结构化结果未完成。', 'invalid_json': '模型返回无法解析成 JSON。', 'request_timeout': '模型请求超时。', 'source_preparation_failed': '原料准备失败，整理尚未开始。', 'call_failed': '整理调用未完成，保留原料与回执等待恢复。', 'brief_refresh_failed': '认知摘要刷新未完成；已完成的各路整理保留，重试可复用。', 'revision_conflict': '整理期间词条版本变化，等待按最新版本重试。', 'interrupted': '整理时进程退出或任务被终止。', 'task_failed': '任务未完成，需要核查日结回执。', 'candidate_review': '有效候选已处理，仍有候选引用复核未通过。', 'topic_annotation_review': '正文已完成；附加主题分类未通过校验，已隔离，等待复核。', 'validation_rejected': '模型候选未通过来源或结构校验，等待复核。', 'automatic_retry_exhausted': '该日自动重试次数已用尽，保留回执等待恢复。', 'staging_error': '历史日修复尚未完成，保留成功批次与失败回执等待恢复。'}
STAGES = {'encode': '批次编码', 'commit': '事件写入', 'projection': '线程投影', 'promotion': '正式提交', 'manifest': '修复清单复核', 'replacement': '事件替代', 'copy': '临时副本准备', 'replan': '来源重新核验'}
ACTOR_REASONS = {'k_actor_evidence_missing': '候选归属于 AI，但所引来源未通过 AI 行动证据校验。', 'human_actor_evidence_missing': '候选归属于人类伙伴，但所引来源未通过人类伙伴行动证据校验。'}

def path():
    return ROOT / 'data' / 'day_end_issues.json'

def read():
    if not path().exists():
        return {'tasks': {}, 'issues': {}}
    return json.loads(path().read_text('utf-8'))

def write(state):
    from cognition.books import atomic_text
    atomic_text(path(), json.dumps(state, ensure_ascii=False, indent=2))

def key(date, lane):
    return hashlib.sha256((date + ':' + lane).encode()).hexdigest()[:24]

def record_task(date, lane, status, message='', failure_stage=''):
    if not re.fullmatch('\\d{4}-\\d{2}-\\d{2}', date or '') or lane not in LABELS:
        return
    with LOCK:
        state = read()
        code = 'interrupted' if '终止' in message else 'task_failed'
        state['tasks'][key(date, lane)] = {'date': date, 'lane': lane, 'status': status, 'code': code, 'instance': INSTANCE, 'failure_stage': failure_stage if failure_stage in {'cognition', 'auxiliary'} else '', 'updated_at': datetime.now().astimezone().isoformat()}
        write(state)

def observed_tasks(state):
    observed = {}
    for item in state['tasks'].values():
        if item['status'] == 'error':
            observed[key(item['date'], item['lane'])] = item
        elif item['status'] == 'running':
            if item.get('instance') != INSTANCE:
                observed[key(item['date'], item['lane'])] = {**item, 'code': 'interrupted'}
            else:
                old = state['issues'].get(key(item['date'], item['lane']))
                if old and (not old.get('resolved')):
                    observed[key(item['date'], item['lane'])] = {k: old[k] for k in ('date', 'lane', 'code', 'updated_at')}
    roots = {}
    for p in (ROOT / 'data/cognition_maintenance/daily_runs').glob('*.json'):
        d = json.loads(p.read_text('utf-8'))
        date = d.get('source_date', '')
        if date not in roots or p.stat().st_mtime_ns > roots[date][0]:
            roots[date] = (p.stat().st_mtime_ns, d)
    for date, (_, d) in roots.items():
        tracked = state['tasks'].get(key(date, 'unified_analysis'), {})
        if tracked.get('failure_stage') == 'cognition':
            observed.pop(key(date, 'unified_analysis'), None)
        from cognition import maintenance
        interrupted = d.get('status') == 'running' and d.get('instance') != maintenance._instance
        for lane, status in d.get('lanes', {}).items():
            if lane not in LABELS:
                continue
            observed.pop(key(date, lane), None)
            if status in {'error', 'interrupted'}:
                from cognition.completion_evidence import find_completion
                try:
                    completion = find_completion(d, lane, ROOT)
                except (OSError, ValueError, KeyError, TypeError):
                    completion = None
                if completion:
                    old = state['issues'].get(key(date, lane))
                    if old:
                        old['resolved_by'] = completion
                    continue
                code = d.get('lane_errors', {}).get(lane, {}).get('code', 'task_failed')
                if code not in REASONS:
                    code = 'validation_rejected'
                observed[key(date, lane)] = {'date': date, 'lane': lane, 'code': code, 'updated_at': d.get('completed_at') or d.get('started_at', '')}
            elif status == 'completed_with_rejections' and lane in {'self', 'interest'}:
                detail = d.get('lane_reference_diagnostics', {}).get(lane, [])
                locations = [{'index': x['index'], 'source_count': x['source_count']} for x in detail if isinstance(x, dict) and type(x.get('index')) is int and (type(x.get('source_count')) is int)]
                rejection = d.get('lane_rejections', {}).get(lane, {})
                topic_only = rejection.get('topic_annotation_count', 0) > 0 and rejection.get('proposal_count', 0) == 0 and (rejection.get('review_count', 0) == 0) and (not detail)
                observed[key(date, lane)] = {'date': date, 'lane': lane, 'code': 'topic_annotation_review' if topic_only else 'candidate_review', 'locations': locations, 'updated_at': d.get('completed_at', '')}
            elif status in {'pending', 'running', 'not_started'}:
                old = state['issues'].get(key(date, lane))
                if interrupted and status in {'pending', 'running'}:
                    observed[key(date, lane)] = {'date': date, 'lane': lane, 'code': 'interrupted', 'updated_at': d.get('started_at', '')}
                elif old and (not old.get('resolved')):
                    observed[key(date, lane)] = {k: old[k] for k in ('date', 'lane', 'code', 'updated_at')}
        if d.get('failure_stage') == 'source_preparation':
            observed[key(date, 'source_preparation')] = {'date': date, 'lane': 'source_preparation', 'code': 'source_preparation_failed', 'updated_at': d.get('completed_at', '')}
    from memory_v2.online_ingest import configuration_from_environment
    from memory_v2.online_recovery import OnlineRetryReceipts, MAX_AUTOMATIC_ATTEMPTS
    config = configuration_from_environment()
    if config:
        receipts = OnlineRetryReceipts(config.memory_db_path)._read()
        for date, d in receipts.items():
            if d.get('status') == 'attempting':
                old = state['issues'].get(key(date, 'memory_v2'))
                if old and (not old.get('resolved')):
                    observed[key(date, 'memory_v2')] = {k: old[k] for k in ('date', 'lane', 'code', 'updated_at')}
                continue
            if d.get('status') != 'error':
                continue
            code = 'automatic_retry_exhausted' if d.get('attempt_count', 0) >= MAX_AUTOMATIC_ATTEMPTS else 'staging_error'
            observed[key(date, 'memory_v2')] = {'date': date, 'lane': 'memory_v2', 'code': code, 'updated_at': datetime.fromtimestamp(d.get('last_attempt_at', 0)).astimezone().isoformat()}
            item = observed[key(date, 'memory_v2')]
            if str(d.get('error_code', '')).startswith('continuation_'):
                item['detail_reason'] = '正式日结已完成，历史事件续接分页尚未完成；此任务不重新生成当天日结。'
                if d.get('continuation_diagnostics', {}).get('status') == 'contract_error':
                    item['detail_reason'] += ' 续接判断的输出合同未通过，请核对分页合同回执。'
            if str(d.get('error_code', '')).startswith('scene_reconstruction_'):
                item['detail_reason'] = '事件日结已完成，派生场景整理尚未完成，需要恢复场景复核。'
                scene_code = d.get('scene_diagnostics', {}).get('error_code')
                from memory_v2.scene_regrouping_output import FORMAT_CODES
                if scene_code in FORMAT_CODES:
                    item['detail_reason'] += ' 场景摘要超过约定字符预算。' if scene_code == 'scene_regroup_summary_budget' else ' 场景重组的片段覆盖或输出结构未通过校验。'
            phase = d.get('repair_diagnostics', {}).get('failure_phase')
            if phase in STAGES:
                item['detail_reason'] = '失败阶段：' + STAGES[phase] + '。'
            diagnostics = d.get('encoding_diagnostics') or []
            if diagnostics:
                last = diagnostics[-1]
                number, actor = (last.get('batch_number'), last.get('error_code'))
                if type(number) is int and number > 0 and (actor in ACTOR_REASONS):
                    item['detail_reason'] = f'第 {number} 批：' + ACTOR_REASONS[actor]
                elif type(number) is int and number > 0 and (last.get('contract_check') == 'aspect_types'):
                    item['detail_reason'] = f'第 {number} 批：事件的次要分类无效或重复，需要复核分类及其来源。'
                elif type(number) is int and number > 0 and (last.get('contract_check') == 'episode_fields'):
                    item['detail_reason'] = f'第 {number} 批：模型输出缺少事件必填字段，严格校验未通过。'
        from memory_v2.conversation_source import ConversationSource
        from memory_v2.scene_planner import planner_for_ingest
        from memory_v2.day_source_coverage import inspect_day_source_coverage
        from memory_v2.day_repair import day_authority_digest
        source = ConversationSource(config.authority_db_path, canonical_sessions_only=True)
        planner = planner_for_ingest(source)
        for iid, old in state['issues'].items():
            if old['lane'] != 'memory_v2' or old.get('resolved') or iid in observed:
                continue
            day = planner.plan_active_date(old['date'])
            coverage = inspect_day_source_coverage(config.memory_db_path, day, source_namespace=planner.config.source_namespace, authority_digest=day_authority_digest(day))
            with closing(sqlite3.connect(Path(config.memory_db_path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
                settled = db.execute('SELECT 1 FROM day_settlements WHERE active_date=? AND source_namespace=? LIMIT 1', (old['date'], planner.config.source_namespace)).fetchone() is not None
            if coverage.source_changed or not (coverage.zero_event_complete or coverage.repaired_complete or settled):
                observed[iid] = {k: old[k] for k in ('date', 'lane', 'code', 'updated_at')}
    return observed

def listing():
    with LOCK:
        state = read()
        before = json.dumps(state['issues'], sort_keys=True)
        observed = observed_tasks(state)
        for iid, item in observed.items():
            old = state['issues'].get(iid, {})
            revision = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:24]
            state['issues'][iid] = {**item, 'id': iid, 'revision': revision, 'resolved': False, 'acknowledged': old.get('acknowledged', False) if old.get('revision') == revision else False}
        for iid, item in state['issues'].items():
            if iid not in observed:
                item['resolved'] = True
        if json.dumps(state['issues'], sort_keys=True) != before:
            write(state)
        items = []
        for item in state['issues'].values():
            if item.get('acknowledged'):
                continue
            lane, code = (item['lane'], item['code'])
            items.append({**item, 'label': LABELS.get(lane, '日总结'), 'reason': REASONS.get(code, REASONS['task_failed'])})
        return {'issues': sorted(items, key=lambda i: (i['date'], i['label']), reverse=True), 'unresolved_count': sum((not i['resolved'] for i in items))}

def acknowledge(iid, revision):
    with LOCK:
        listing()
        state = read()
        item = state['issues'].get(iid)
        if not item or not item['resolved'] or item['revision'] != revision:
            return False
        item['acknowledged'] = True
        write(state)
        return True
