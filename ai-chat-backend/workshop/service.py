"""Shared domain API for MCP, autonomous visits and the read-only workbench UI."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

from .runner import AppServer
from .store import MODELS, Store, now
from .lease import WriterLease
from .capabilities import describe

logger = logging.getLogger(__name__)
IMPORTANT = {'accepted', 'plan', 'confirmed', 'ready', 'accepted_work', 'paused', 'budget_paused', 'failed', 'canceled', 'recovery_required', 'modified', 'published', 'inspiration_added', 'budget_calibration', 'resource_approval', 'quality_queued', 'quality_report', 'quality_blocked', 'desktop_waiting', 'usage_reconciled'}
IMPORTANT.add('budget_policy')
IMPORTANT.update(('decision_required','decision_answered'))
IMPORTANT.add('cabinet_visited')
IMPORTANT.add('artifact_shared')


class Workshop:
    def __init__(self, store=None, runner=None, runner_factory=None):
        self.store = store or Store()
        self.runner = runner or AppServer(self.store)
        self.quota = {'remaining': None, 'checked_at': None, 'status': 'unknown', 'models': []}
        self.native_capabilities = {}
        from .signals import WakeSignal
        self.wake = WakeSignal()
        self.collaboration_task = None
        self.task = None
        from .execution_pool import ExecutionPool
        self.pool = ExecutionPool(self, runner_factory or (lambda: AppServer(self.store)))
        self.sender = None
        self.share_sender = None
        self.gift_task = None
        self.gift_retry_at = 0
        self.mutation = asyncio.Lock()
        self.lease = None
        from .desktop_activity import DesktopActivity
        self.desktop_gate = DesktopActivity()
        self.desktop_activity = {'status':'unknown', 'ready':False, 'active_tasks':0, 'checked_at':None}

    def start(self):
        if self.task is None or self.task.done():
            if self.lease is None:
                self.lease = WriterLease(self.store.directory / 'writer.lock')
            self.store.recover()
            from .residency_store import recover as recover_collaborations
            recover_collaborations(self)
            from .account_meter import disable
            disable(self.store)
            from .quality import recover
            recover(self.store)
            self.task = asyncio.create_task(self.loop())
        self.wake.set()

    async def close(self):
        from .residency import close as close_collaboration
        await close_collaboration(self)
        if self.gift_task:
            self.gift_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.gift_task
        if self.task:
            self.task.cancel()
        if self.task:
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.pool.close()
        await self.runner.close()
        if self.lease:
            self.lease.close()
            self.lease = None

    def snapshot(self, offset=0, *, audience='k'):
        from .budget import references, summary
        cfg = self.store.settings()
        jobs = self.store.jobs()
        from .cabinet import archived_ids
        archived = archived_ids(self.store)
        jobs = [row for row in jobs if row['id'] not in archived]
        from .quality import summary as quality_summary
        for row in jobs:
            row['quality_summary'] = quality_summary(self.store,row)
            from .delivery_policy import job_status
            row.update(job_status(self.store,row))
        quota = dict(self.quota)
        if not quota['checked_at'] or time.time()-quota['checked_at'] > 90:
            quota.update(remaining=None, status='unknown')
        active = self.pool.active()
        current = next((row for row in jobs if row['id'] in active), None)
        for job in jobs:
            reasons = []
            if job['state'] in ('queued','account_paused'):
                if cfg['desktop_idle_only'] and not self.desktop_activity['ready']:
                    reasons.append('桌面 Codex 正在执行任务，等候空闲' if self.desktop_activity['status']=='busy' else
                        '桌面状态待核对' if self.desktop_activity['status']=='unknown' else '桌面已空闲，短暂等候额度回执稳定')
                if quota['remaining'] is None:
                    reasons.append('执行器额度暂未确认')
                elif quota['remaining'] < cfg['remaining_floor']:
                    reasons.append(f"剩余额度 {quota['remaining']:g}% 低于开工下限 {cfg['remaining_floor']}%")
                if not self.pool.available():
                    reasons.append(f"执行名额已满（{len(active)}/{cfg['max_parallel']}），等制作或质检结束")
                if not cfg['enabled']:
                    reasons.append('工作坊已关闭自动开工')
                if quota['models'] and (job['model'] or cfg['model']) not in quota['models']:
                    reasons.append('所选模型不在执行器当前可用目录')
                job['waiting_reasons'] = reasons
        total_jobs = len(jobs)
        offset = max(0, int(offset))
        visible = [{key: value for key, value in row.items() if key not in ('plan', 'pending_text', 'thread_id', 'turn_id')}
                   for row in jobs[offset:offset+50]]
        for row in visible:
            row['brief'] = row['brief'][:600]
            row['budget_summary'] = summary(row, limits_enabled=cfg['budget_limits_enabled'])
            from .account_meter import summary as meter_summary
            row['account_usage']=meter_summary(self.store,row['id'])
        if audience == 'public':
            from .privacy import public_job
            visible = [public_job(row) for row in visible]
        from .creative_templates import TEMPLATES
        from .parallel_meter import summary as parallel_summary
        return {'execution_pool':self.pool.snapshot(), 'workshop_account_usage':parallel_summary(self.store), 'creative_templates': TEMPLATES, 'settings': cfg, 'quota': quota, 'desktop_activity':self.desktop_activity, 'jobs': visible, 'total_jobs': total_jobs, 'archived_jobs_count':len(archived), 'offset': offset,
                'has_more': offset+len(visible) < total_jobs, 'current_job_id': current['id'] if current else None,
                'capabilities': describe(self.native_capabilities if quota['checked_at'] and time.time()-quota['checked_at'] <= 90 else {}),
                'budget_references': references(self.store),
                'budget_note': ('直接开工不统计账号额度下降，实际 token 回执仍保存；账号最低剩余额度继续控制开工。' if not cfg['desktop_idle_only'] else '只累计实际开工段内观察到的账号额度下降，暂停间隔排除；官方回执延迟可能影响边界。桌面忙时等候。') + (' 预算限制暂时关闭。' if not cfg['budget_limits_enabled'] else ' 制作与质检仍执行各自预算；百分比按工单 token 与独立参考估算。')}

    async def refresh(self, model=None):
        cfg = self.store.settings()
        head = next((row for row in self.store.jobs() if row['state'] == 'queued'), None)
        model = model or (head or {}).get('model') or cfg['model']
        try:
            result = await self.runner.metadata(model)
            self.native_capabilities = result.get('native_capabilities', {})
            models = [row.get('model') or row.get('id') for row in result['models']]
            from .reasoning import catalog_options
            from .quality import MODELS as REVIEW_MODELS
            # Report only supported families, retain exact catalog IDs.
            self.quota = {'remaining': result['remaining'], 'checked_at': time.time(), 'status': result.get('quota_status') or ('ok' if result['remaining'] is not None else 'unknown'),
                          'models': [value for value in models if value in MODELS],
                          'review_models': [value for value in models if value in REVIEW_MODELS],
                          'reasoning_options':catalog_options(result['models'])}
        except Exception as exc:
            self.native_capabilities = {}
            self.quota = {'remaining': None, 'checked_at': time.time(), 'status': type(exc).__name__, 'models': []}
        return self.snapshot()

    async def action(self, args):
        async with self.mutation:
            action = args.get('action', 'list')
            command_key = 'action:' + str(args['request_id']) if args.get('request_id') and action not in ('list', 'room', 'works', 'create', 'inspect') else None
            if command_key:
                with self.store.db() as db:
                    saved = db.execute('SELECT result FROM commands WHERE key=?', (command_key,)).fetchone()
                if saved:
                    return json.loads(saved[0])
            if action == 'list':
                return self.snapshot(args.get('offset', 0))
            if action == 'room':
                return self.store.room(args['job_id'], int(args.get('offset', 0)))
            if action == 'works':
                from .catalog import works
                return {'cabinet':works(self, args.get('offset',0))}
            if action == 'create':
                result = self.store.create(args['title'], args['text'], args['request_id'], args.get('visibility', 'public'))
                self.wake.set()
                return {'accepted': result, **self.snapshot()}
            job = self.store.job(args['job_id'])
            job_id = job['id']
            expected = args.get('revision')
            if expected is None or expected != job['revision']:
                raise ValueError('revision_required_or_stale')
            running = job['state'] in ('executing', 'discussing', 'stopping')
            if action == 'publish':
                if job['visibility'] == 'private':
                    self.store.update(job_id, visibility='public')
                    self.store.event(job_id, 'published', {'source': 'AI 的结构化公开决定', 'revision': job['revision'],
                                     'summary': 'AI 已公开这份工单，现在可查看讨论、作品并补充灵感。'})
            elif action in ('message', 'edit'):
                text = args['text']
                message_seq = self.store.message(job_id, 'k', text)
                fields = {'pending_text': (job['pending_text'] + '\n' + text).strip()}
                new_revision=action=='edit' or job['state'] in ('accepted','awaiting_review')
                if new_revision:
                    fields.update(revision=job['revision']+1, confirmed_version=0, plan='', resume_state='discussing')
                    if action=='edit':fields['brief']=text
                    if job['state'] in ('accepted', 'canceled'):
                        with self.store.db() as db:
                            fields['queue_seq'] = db.execute('SELECT coalesce(max(queue_seq),0)+1 FROM jobs').fetchone()[0]
                    from .action_evidence import k_action
                    self.store.event(job_id, 'modified', {'text': text, 'revision': job['revision']+1,
                        **k_action('modify', job['revision']+1, message_seq=message_seq),
                        'execution_receipt': '已收到；下一安全节点重新讨论'})
                if not running and job['state'] not in ('budget_paused', 'paused', 'recovery_required'):
                    fields['state'] = 'queued'
                if action=='message' and job['state']=='awaiting_input' and job['reason']=='k_decision_required':
                    fields['state']='awaiting_input'
                self.store.update(job_id, **fields)
            elif action == 'confirm':
                from .decisions import pending
                if pending(self.store,job_id):
                    return {**self.snapshot(),'confirmation':{'status':'answer_required',
                        'summary':'当前有工具手问题待回答，请用 answer 提交 decision_id 与 answers；方案确认和问题答复是两个记录。'}}
                if args.get('plan_version') == job['plan_version'] == job['confirmed_version'] and job['confirmed_version']:
                    return {**self.snapshot(), 'confirmation': {'status':'already_confirmed',
                        'job_id':job_id,'plan_version':job['plan_version'],'state':job['state'],
                        'reason':job['reason'], 'summary':'该方案已确认；重复确认不重启执行，也不解除已有卡点。'}}
                confirmable = ('awaiting_confirmation', 'awaiting_input', 'queued', 'account_paused', 'paused', 'budget_paused')
                if job['state'] not in confirmable or not job['plan'] or args.get('plan_version') != job['plan_version']:
                    raise ValueError('current_plan_confirmation_required')
                held = job['state'] in ('account_paused', 'paused', 'budget_paused')
                self.store.update(job_id, confirmed_version=job['plan_version'],
                                  state=job['state'] if held else 'queued',
                                  resume_state='executing' if held else '',
                                  reason=job['reason'] if held else '')
                from .action_evidence import k_action
                self.store.event(job_id, 'confirmed', {'plan_version': job['plan_version'], 'revision': job['revision'],
                    **k_action('confirm', job['revision'], plan_version=job['plan_version'])})
            elif action == 'answer':
                from .decisions import answer, pending
                fresh=any(row['id']==args.get('decision_id') for row in pending(self.store,job_id))
                result=answer(self.store,job,args)
                self.wake.set()
                return {'decision':result,'decision_submission':{'status':'saved' if fresh else 'already_answered',
                    'job_state':self.store.job(job_id)['state']},**self.snapshot()}
            elif action in ('pause', 'cancel', 'delete'):
                target = 'paused' if action == 'pause' else 'canceled'
                if job_id in self.pool.active() and self.pool.active()[job_id]['kind'] == 'review':
                    self.store.update(job_id, state=target, resume_state=job['state'], reason=action, deleted=int(action=='delete'))
                    await self.pool.interrupt(job_id)
                    self.store.event(job_id, 'paused' if action=='pause' else 'canceled', {'action':action, 'revision':job['revision']})
                    self.wake.set()
                    return self.snapshot()
                if running:
                    self.store.update(job_id, state='stopping', resume_state=job['state'], reason=action)
                    await self.pool.interrupt(job_id)
                    # Slot remains held until turn/completed acknowledges stopping.
                else:
                    self.store.update(job_id, state=target, resume_state=job['state'], reason=action, deleted=int(action == 'delete'))
                    self.store.event(job_id, 'paused' if action == 'pause' else 'canceled', {'action': action, 'revision': job['revision']})
            elif action == 'resume':
                if job['state'] != 'paused':
                    raise ValueError('explicit_pause_required_budget_needs_human_approval')
                self.store.update(job_id, state='queued', reason='')
            elif action == 'reconcile':
                if job['state'] != 'recovery_required' or not job['thread_id']:
                    raise ValueError('recovery_thread_required')
                from .recovery import recover_unstarted, read_thread
                if await recover_unstarted(self, job, 'k_technical_reconcile'):
                    return self.snapshot()
                result = await read_thread(self.runner, job['thread_id'])
                turns = result.get('thread', {}).get('turns') or []
                last = next((turn for turn in turns if turn.get('id') == job['turn_id']), None)
                if not last or last.get('status') not in ('completed', 'interrupted', 'failed'):
                    raise ValueError('previous_execution_not_confirmed_stopped')
                from .progress import current_artifacts
                artifacts = current_artifacts(self.store,job)
                for row in artifacts:
                    self.artifact_path(row)
                phase = 'awaiting_review' if artifacts and last['status'] == 'completed' else 'awaiting_confirmation' if job['plan_version'] != job['confirmed_version'] and job['plan'] else 'paused'
                exhausted = self.store.settings()['budget_limits_enabled'] and job['tokens'] >= job['token_cap']
                self.store.update(job_id, state='budget_paused' if exhausted else 'paused', resume_state=phase, reason='token_budget' if exhausted else 'reconciled')
                self.store.event(job_id, 'reconciled', {'turn_status': last['status'], 'artifact_ids': [row['id'] for row in artifacts], 'resume_state': phase})
            elif action == 'accept':
                if job['state'] != 'awaiting_review' or not args.get('artifact_id'):
                    raise ValueError('current_artifact_review_required')
                artifact = self.artifact(args['artifact_id'])
                if artifact['job_id'] != job_id or artifact['revision'] != job['revision']:
                    raise ValueError('stale_artifact')
                from .progress import current_artifacts
                if artifact['id'] not in {row['id'] for row in current_artifacts(self.store,job)}:
                    raise ValueError('stale_artifact')
                from .delivery_policy import job_status
                if not job_status(self.store,job)['review_ready']:
                    raise ValueError('delivery_review_required')
                self.store.update(job_id, state='accepted')
                from .action_evidence import k_action
                self.store.event(job_id, 'accepted_work', {'artifact_id': artifact['id'], 'revision': job['revision'],
                    **k_action('accept', job['revision'], artifact_id=artifact['id'])})
            elif action == 'share':
                from .sharing import share
                return {'share':share(self,job,[self.artifact(args['artifact_id'])],source='k',text=args.get('text','')), **self.snapshot()}
            elif action == 'finish':
                from .completion import finish
                return finish(self,job,args)
            elif action == 'review':
                artifact = self.artifact(args['artifact_id'])
                if artifact['job_id'] != job_id or artifact['revision'] != job['revision']:
                    raise ValueError('stale_artifact')
                from .quality import enqueue
                enqueue(self, artifact, retry=True)
            elif action == 'inspect':
                artifact = self.artifact(args['artifact_id'])
                if artifact['job_id'] != job_id:
                    raise ValueError('wrong_work_order')
                path = self.artifact_path(artifact)
                if path.suffix.lower() == '.html':
                    from .browser import inspect_html
                    screenshot = self.store.directory / 'works' / job_id / ('k-review-' + artifact['id'] + '.png')
                    material = await inspect_html(path, screenshot, args.get('selector', ''), actions=args.get('browser_actions'))
                    self.store.event(job_id, 'inspected_browser', {'artifact_id': artifact['id'], 'revision': artifact['revision'],
                        **{key:material[key] for key in ('page_errors','console_errors','checks','interaction','status','viewport')}})
                    return {'artifact': artifact, 'browser_material': material, 'visual_attachment': str(screenshot), 'preview_url': f"/api/workshop/artifacts/{artifact['id']}/preview"}
                if path.suffix.lower() in ('.txt', '.md', '.css', '.js', '.json'):
                    text = path.read_text(encoding='utf-8')[:20000]
                    self.store.event(job_id, 'inspected_text', {'artifact_id': artifact['id'], 'revision': artifact['revision'], 'material': '实际正文，非浏览器交互预览'})
                    return {'artifact': artifact, 'text': text, 'truncated': path.stat().st_size > 20000, 'preview_url': f"/api/workshop/artifacts/{artifact['id']}/preview"}
                if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                    return {'artifact': artifact, 'visual_attachment': str(path), 'material': '实际图片附件', 'preview_url': f"/api/workshop/artifacts/{artifact['id']}/preview"}
                return {'artifact': artifact, 'material': '附件已定位；尚未传入视觉', 'preview_url': f"/api/workshop/artifacts/{artifact['id']}/preview"}
            else:
                raise ValueError('unknown_workshop_action')
            self.wake.set()
            result = self.snapshot()
            if command_key:
                with self.store.db() as db:
                    db.execute('INSERT OR IGNORE INTO commands VALUES(?,?)', (command_key, json.dumps(result, ensure_ascii=False)))
            return result

    def propose(self, job_id, plan, revision):
        job = self.store.job(job_id)
        if job['revision'] != revision or not isinstance(plan, str) or not plan.strip() or len(plan) > 30000:
            raise ValueError('invalid_or_stale_plan')
        version = job['plan_version']+1
        self.store.update(job_id, plan=plan, plan_version=version, confirmed_version=0)
        self.store.event(job_id, 'plan', {'plan': plan, 'plan_version': version, 'revision': revision})
        return {'plan_version': version, 'status': '等待 AI 结构化确认'}

    def deliver(self, job_id, args, revision):
        root = (self.store.directory / 'works' / job_id).resolve()
        path = Path(args['path'])
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 20*1024*1024:
            raise ValueError('artifact_must_be_real_bounded_work_file')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.store.db() as db:
            existing = db.execute('SELECT * FROM artifacts WHERE job_id=? AND revision=? AND name=? AND sha256=?',
                                 (job_id, revision, path.name, digest)).fetchone()
        if existing:
            record = dict(existing)
            self.artifact_path(record)
            self.store.event(job_id, 'delivery_referenced', {'artifact_id': record['id'], 'revision': revision,
                             'summary': args['summary'], 'checks': args['checks']})
            return {'artifact_id': record['id'], 'revision': revision, 'sha256': digest,
                    'preview_url': f"/api/workshop/artifacts/{record['id']}/preview"}
        # Symlinks/reparse targets cannot export secrets outside the work root.
        artifact_id = uuid.uuid4().hex
        dest = self.store.directory / 'artifacts' / artifact_id
        dest.mkdir(parents=True)
        shutil.copyfile(path, dest / path.name)
        digest = hashlib.sha256((dest / path.name).read_bytes()).hexdigest()
        with self.store.db() as db:
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)', (artifact_id, job_id, revision, path.name, digest, args['summary'], args['checks'], now()))
        self.store.event(job_id, 'delivered', {'artifact_id': artifact_id, 'revision': revision, 'name': path.name, 'summary': args['summary'], 'checks': args['checks']})
        return {'artifact_id': artifact_id, 'revision': revision, 'sha256': digest, 'preview_url': f'/api/workshop/artifacts/{artifact_id}/preview'}

    def artifact(self, artifact_id):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
        if not row:
            raise KeyError('artifact_not_found')
        record = dict(row)
        self.store.job(record['job_id'])
        return record

    def artifact_path(self, artifact):
        root = (self.store.directory / 'artifacts' / artifact['id']).resolve()
        path = (root / artifact['name']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('artifact_unavailable')
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
            raise ValueError('artifact_changed_after_delivery')
        return path

    async def approve(self, job_id, addition, mode='continue', *, source='human_settings'):
        async with self.mutation:
            job = self.store.job(job_id)
            if job['state'] != 'budget_paused' or type(addition) is not int or not 1000 <= addition <= 10000000:
                raise ValueError('invalid_budget_approval')
            if mode not in ('continue', 'discuss'):
                raise ValueError('invalid_approval_mode')
            fields = {'token_cap': max(job['tokens'], job['token_cap'])+addition, 'state': 'queued', 'reason': ''}
            if mode == 'discuss':
                fields.update(confirmed_version=0, resume_state='', pending_text=(job['pending_text'] + '\n[人类伙伴的资源批准：重新讨论方向]\n请先重新讨论方向、缩小范围；保留已有作品。AI 再次确认方案后才制作。').strip())
            self.store.update(job_id, **fields)
            self.store.event(job_id, 'resource_approval', {'source': source, 'addition': addition, 'consumed': job['tokens']})
            self.wake.set()
        return self.snapshot()

    async def project(self):
        if not self.sender:
            return
        for receipt in self.store.pending_events(projection=True):
            from .invitation import KINDS, card as visit_card
            if receipt['kind'] in KINDS:
                card = visit_card(receipt, self)
                result = await self.sender(session_id=receipt['body']['session_id'], source_id=receipt['id'],
                    content=card['summary'], event_type='workshop_event', metadata={'items': [
                        {'tool': 'workshop', 'arguments': {}, 'status': 'failed' if receipt['kind'] in ('visit_failed', 'visit_interrupted') else 'success',
                         'description': '工作坊快捷来访记录', 'extra_data': {'workshop': card}}]})
                if not result or (result.get('mutated') is False and result.get('reason') != 'already_persisted'):
                    continue
            elif receipt['kind'] == 'cabinet_visited':
                from .cabinet import notification
                card = notification(receipt)
                result = await self.sender(session_id='', source_id=receipt['id'], content=card['summary'],
                    event_type='workshop_event', metadata={'items':[
                        {'tool':'workshop', 'arguments':{}, 'result':'已记录', 'extra_data':{'workshop':card}}]})
                if not result or (result.get('mutated') is False and result.get('reason') != 'already_persisted'):
                    continue
            elif receipt['kind'] == 'artifact_shared':
                job = self.store.job(receipt['job_id'], include_deleted=True)
                if not job['deleted'] and job.get('visibility') == 'public':
                    from .sharing import card as share_card
                    try:
                        card = share_card(self,job,receipt)
                    except (KeyError,ValueError,OSError):
                        continue
                    from .chat_delivery import schedule, deliver
                    if card.get('source') != 'k':
                        schedule(self, receipt)
                        continue
                    if not await deliver(self, receipt, card):
                        continue
            elif receipt['kind'] in IMPORTANT:
                job = self.store.job(receipt['job_id'], include_deleted=True)
                if receipt['kind'] == 'ready':
                    from .delivery_policy import classify
                    try:
                        artifacts = [self.artifact(artifact_id) for artifact_id in receipt['body'].get('artifact_ids',[])]
                        light = bool(artifacts) and all(classify(self.artifact_path(row)) != 'standard' for row in artifacts)
                    except (KeyError,ValueError,OSError):
                        light = False
                    if light:
                        self.store.acknowledge([receipt['id']],projection=True)
                        self.store.acknowledge([receipt['id']])
                        continue
                if not job['deleted']:
                    from .privacy import public_card
                    card = public_card(job, receipt)
                    result = await self.sender(session_id='', source_id=receipt['id'],
                        content=f"工作坊 · {card['title']} · {receipt['kind']}\n" + json.dumps(card, ensure_ascii=False)[:1800],
                        event_type='workshop_event', metadata={'items': [
                            {'tool': 'workshop', 'arguments': {}, 'result': '已记录', 'extra_data': {'workshop': card}}]})
                    if not result or (result.get('mutated') is False and result.get('reason') != 'already_persisted'):
                        continue
            # A tombstone in this durable outbox prevents reinsertion after UI deletion.
            self.store.acknowledge([receipt['id']], projection=True)
            if receipt['kind'] not in IMPORTANT:
                # Raw transport/tool logs are room history, not AI notifications.
                self.store.acknowledge([receipt['id']])

    async def loop(self):
        while True:
            try:
                self.wake.clear()
                from .budget import settle_calibrations
                settle_calibrations(self.store)
                from .budget_policy import settle as settle_budget_policy
                settle_budget_policy(self.store)
                from .usage_recovery import settle as settle_usage
                await asyncio.to_thread(settle_usage,self)
                cfg = self.store.settings()
                if cfg['desktop_idle_only']:
                    self.desktop_activity = await asyncio.to_thread(self.desktop_gate.read)
                    if not self.desktop_activity['ready']:
                        from .budget import wait_for_desktop
                        for waiting in self.store.jobs():
                            if waiting['state'] not in ('executing','discussing','stopping'):
                                wait_for_desktop(self.store,waiting['id'])
                    if not self.desktop_activity['ready']:
                        for job_id, lane in self.pool.active().items():
                            active_job = self.store.job(job_id, include_deleted=True)
                            if lane['kind'] != 'production' or active_job['state'] not in ('discussing','executing'):
                                continue
                            reason = 'desktop_busy' if self.desktop_activity['status']=='busy' else 'desktop_unknown'
                            self.store.update(active_job['id'], state='stopping', resume_state=active_job['state'], reason=reason)
                            self.store.event(active_job['id'], 'desktop_waiting', {'revision':active_job['revision'],
                                'reason':reason, 'summary':'桌面 Codex 有任务或状态待核对；工作坊在安全节点暂停，空闲后自动续跑，不追加额度。'})
                if not self.quota['checked_at'] or time.time()-self.quota['checked_at'] >= 60:
                    await self.refresh()
                await self.project()
                from .dispatch import tick
                await tick(self)
                from .residency import schedule as schedule_collaboration
                schedule_collaboration(self)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), 2 if cfg['desktop_idle_only'] else 30)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('workshop dispatcher failed')
                await asyncio.sleep(5)

    async def execute(self, job, runner=None):
        try:
            if self.store.settings()['budget_limits_enabled'] and (not job['usage_known'] or job['tokens'] >= job['token_cap']):
                self.store.update(job['id'], state='budget_paused', resume_state=job['state'], reason='token_budget' if job['usage_known'] else 'usage_unknown')
                self.store.event(job['id'], 'budget_paused', {'reason':'token_budget' if job['usage_known'] else 'usage_unknown', 'tokens': job['tokens'], 'token_cap': job['token_cap'], 'model': job['model'], 'stage': job['state']})
                return
            if job['resume_state'] in ('awaiting_review', 'awaiting_confirmation'):
                self.store.update(job['id'], state=job['resume_state'], resume_state='')
                return
            result = await (runner or self.runner).run(job, self)
            current = self.store.job(job['id'])
            if current['state'] == 'stopping':
                reason = current['reason']
                target = 'budget_paused' if reason in ('token_budget', 'usage_unknown', 'calibration_required', 'calibration_limit', 'calibration_reset', 'calibration_unavailable', 'calibration_expired') else 'account_paused' if reason in ('quota_low', 'quota_unknown', 'desktop_busy', 'desktop_unknown') else 'paused' if reason not in ('cancel', 'delete') else 'canceled'
                if reason=='k_decision_required':target='awaiting_input'
                from .progress import current_artifacts
                completed_artifacts = current_artifacts(self.store,current)
                resume = 'awaiting_review' if result['status'] == 'completed' and completed_artifacts else 'awaiting_confirmation' if result['status'] == 'completed' and current['plan_version'] != current['confirmed_version'] and current['plan'] else job['state']
                self.store.update(job['id'], state=target, resume_state=resume, deleted=int(reason == 'delete'))
                self.store.event(job['id'], target, {'reason': reason, 'tokens': current['tokens'], 'token_cap': current['token_cap'], 'model': current['model'], 'stage': job['state']})
            elif self.store.settings()['budget_limits_enabled'] and not current['usage_known']:
                self.store.update(job['id'], state='budget_paused', reason='usage_unknown', resume_state=job['state'])
                self.store.event(job['id'], 'budget_paused', {'reason': 'usage_unknown', 'stage': job['state']})
            elif result.get('decision_pending'):
                self.store.update(job['id'],state='awaiting_input',reason='k_decision_required',resume_state=job['state'])
            elif result['status'] == 'interrupted':
                self.store.update(job['id'], state='paused', reason='interrupted', resume_state=job['state'])
            elif current['revision'] != job['revision'] or current['pending_text']:
                self.store.update(job['id'], state='queued', reason='', resume_state='')
                self.store.event(job['id'], 'modified', {'revision': current['revision'], 'execution_receipt': '原回合已结束；未处理的补充已排队，按当前确认状态继续讨论或制作，旧版成果保留'})
            elif current['plan_version'] != current['confirmed_version'] and current['plan']:
                self.store.update(job['id'], state='awaiting_confirmation')
            elif job['state'] == 'executing':
                from .progress import current_artifacts
                artifacts = current_artifacts(self.store, current)
                self.store.update(job['id'], state='awaiting_review' if artifacts else 'recovery_required',
                    reason='' if artifacts else 'execution_no_delivery', resume_state='' if artifacts else 'executing')
                self.store.event(job['id'], 'ready' if artifacts else 'failed', {'revision': job['revision'],
                    'stage':'executing', 'reason':'' if artifacts else 'execution_no_delivery',
                    'summary':'作品已交付，进入独立质检。' if artifacts else '制作回合结束但没有本版交付；请查看工具手最后回复与工具错误。方案确认保留，停止重复邀请，修复执行卡点后再恢复。',
                    'artifact_ids': [item['id'] for item in artifacts], 'checks': [item['checks'] for item in artifacts]})
            else:
                self.store.update(job['id'], state='awaiting_input')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.update(job['id'], state='recovery_required', reason=type(exc).__name__, resume_state=job['state'])
            diagnosis = {key:getattr(exc, key) for key in ('method', 'category', 'code') if hasattr(exc, key)}
            self.store.event(job['id'], 'failed', {'error': type(exc).__name__, 'stage': job['state'], **diagnosis, 'message': '现场保留，核对后恢复'})
        finally:
            from .budget import settle_calibrations
            settle_calibrations(self.store)
            self.wake.set()


_service = None


def get_service():
    global _service
    if _service is None:
        _service = Workshop()
    return _service
