"""Idle inbox admission and recovery; domain attention/reply ledgers own side effects."""
import asyncio
import hashlib
import json
import logging
import time

logger = logging.getLogger(__name__)
TRIGGER = 'xhs_interactions_idle'


def available():
    try:
        from xhs_interactions import native
        return getattr(native, 'available', lambda: False)() is True
    except Exception:
        return False


def select_work(store, session_id, account_id):
    unfinished = store.unfinished_attentions(session_id, account_id)
    if unfinished:
        row = unfinished[0]
        return {'interaction_ids': row['interaction_ids'], 'batch_id': row['batch_id'],
                'session_id': session_id, 'account_id': account_id,
                'admission_key': row['batch_id'] + ':' + row['state'] + ':' + str(row.get('revision', 0))}
    ids = sorted(row['id'] for row in store.pending(session_id, account_id, limit=3))
    if not ids:
        return None
    return {'interaction_ids': ids, 'session_id': session_id, 'account_id': account_id,
            'admission_key': hashlib.sha256(json.dumps(ids).encode()).hexdigest()}


def attention_snapshot(store, session_id, batch_id):
    """Read receipts only. Never resume a model call, prepare, or submit here."""
    from xhs_interactions.store import digest, reply_receipt
    with store.connect() as db:
        row = db.execute('SELECT record FROM attention WHERE batch_id=? AND session_id=?',
                         (batch_id, session_id)).fetchone()
    if not row:
        return {'status': 'interaction_interrupted', 'xhs_interactions': [], 'reflection': ''}
    data = json.loads(row[0])
    if data['state'] == 'completed':
        return data['result']
    results = list(data.get('action_results') or [])
    recorded = {row.get('interaction_id') for row in results}
    decision = data.get('decision') or {}
    for choice in decision.get('decisions') or []:
        identity = choice['interaction_id']
        if identity in recorded:
            continue
        if choice['action'] == 'leave':
            results.append({'status': 'read_without_reply', 'interaction_id': identity,
                            'reason': choice.get('reason', '')})
        else:
            source_key = 'attention:' + batch_id + ':' + identity
            reply = store.reply(digest([session_id, identity, source_key]), session_id)
            if reply:
                results.append({'status': reply['state'], 'interaction_id': identity,
                                'xhs_reply': reply_receipt(reply), 'reason': choice.get('reason', '')})
            else:
                # A foreground reply may have won the canonical target claim
                # before this attempt returned. Its real receipt stays authoritative.
                incoming = store.get(identity, session_id)
                existing = (store.project(incoming).get('existing_reply') if incoming else None)
                if existing:
                    results.append({'status': existing['state'], 'interaction_id': identity,
                                    'xhs_reply': existing, 'reason': choice.get('reason', '')})
    return {'status': 'interaction_interrupted', 'batch_id': batch_id, 'attention_stage': data['state'],
            'materials': data.get('materials') or [], 'xhs_interactions': results,
            'k_received': data['state'] in {'model_returned', 'decided', 'executing', 'invalid_model_result'},
            'reflection': decision.get('reflection', '')}


def recover_node(runtime_store, node, *, store=None):
    from xhs_interactions.store import InteractionStore
    from xhs_interactions.context import brief as project
    if node.get('execution_status') == 'succeeded':
        return node.get('source_payload') or {}
    refs = node.get('side_effect_refs') or {}
    batch = refs.get('xhs_attention_batch_id')
    activity = runtime_store.get_activity(node['activity_id'])
    run = runtime_store.get_run(activity['run_id'])
    payload = (attention_snapshot(store or InteractionStore(), run['session_id'], batch) if batch else
               {'status': 'interaction_interrupted', 'xhs_interactions': [], 'reflection': ''})
    payload = {**payload, 'interaction_version': 1}
    from .runtime_models import WanderNode, NodeState
    item = WanderNode(**{**node, 'state': NodeState(node['state'])})
    item.source_payload = payload
    item.source_summary = project(payload)
    item.execution_status = 'succeeded' if payload.get('status') == 'completed' else 'failed'
    item.completion_signal = payload.get('status', 'interaction_interrupted')
    item.execution_error = '' if item.execution_status == 'succeeded' else 'interaction_interrupted'
    item.retry_safe = False
    runtime_store.save_node(item)
    return payload


async def execute_node(runtime_store, activity, node_id, context):
    from .node_execution_adapter import NodeExecutionResult, NodeExecutionStatus
    from xhs_interactions.context import brief as project
    if not available():
        return NodeExecutionResult(NodeExecutionStatus.SKIPPED, summary='小红书互动执行器尚未通过真机验收。',
                                   error='interaction_device_validation_required', retry_safe=False)
    from xhs_interactions.autonomy import attend
    from xhs_interactions.store import InteractionStore
    from xhs_publish.store import account_config
    store = InteractionStore()
    current = runtime_store.get_node(node_id) or {}
    ids = (current.get('source_payload') or {}).get('xhs_interaction_ids')
    if ids is None:
        work = select_work(store, context.session_id, account_config()['account_id'])
        ids = work['interaction_ids'] if work else None
    try:
        payload = await attend(runtime_store, activity, context, interaction_ids=ids, node_id=node_id, store=store)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Preserve any durably returned AI decision/reply even when orchestration failed.
        node = runtime_store.get_node(node_id) or current
        batch = (node.get('side_effect_refs') or {}).get('xhs_attention_batch_id')
        payload = attention_snapshot(store, context.session_id, batch) if batch else {
            'status': 'interaction_unavailable', 'xhs_interactions': [], 'reflection': ''}
        logger.warning('XHS interaction node stopped; domain receipts retained')
    latest = runtime_store.get_node(node_id) or {}
    payload = {**payload, 'interaction_version': 1}
    refs = dict(latest.get('side_effect_refs') or {})
    if payload.get('batch_id'):
        refs['xhs_attention_batch_id'] = payload['batch_id']
    good = payload.get('status') == 'completed'
    skipped = payload.get('status') in {'no_pending_interactions', 'interaction_decision_unknown', 'already_running'}
    return NodeExecutionResult(NodeExecutionStatus.SUCCEEDED if good else
                               NodeExecutionStatus.SKIPPED if skipped else NodeExecutionStatus.FAILED,
                               completion_signal=payload.get('status', 'interaction_incomplete'),
                               summary=project(payload), payload=payload, side_effect_refs=refs,
                               error='' if good else payload.get('status', 'interaction_incomplete'),
                               retry_safe=False, audit_model='xhs_interaction_domain')


def summary(nodes):
    from xhs_interactions.context import brief
    return '\n'.join(brief(row.get('source_payload') or {}, include_feeling=False, reference=False)
                     for row in nodes) or '没有新互动材料或回复。'


def recover_idle_run(runner, run):
    """Close only the runtime attempt; resumable domain stages stay discoverable."""
    from .runtime_models import SettlementReason
    for activity in runner.store.list_activities(run['run_id']):
        if activity['state'] in {'completed', 'interrupted', 'aborted'}:
            continue
        for node in runner.store.list_nodes(activity['activity_id']):
            if node['state'] not in {'completed', 'aborted', 'failed'}:
                payload = recover_node(runner.store, node)
                runner.controller.complete_node_review(node['node_id'], reflection=payload.get('reflection', ''),
                    continue_activity=False, abort_reason='process_exit',
                    execution_failed=payload.get('status') != 'completed')
        if runner.share_adapter:
            runner.share_adapter.prepare_activity_event(activity['activity_id'])
        runner.controller.settle_activity(activity['activity_id'], SettlementReason.USER_INTERRUPT,
            summary=summary(runner.store.list_nodes(activity['activity_id'])), share_decision=False)


async def execute_idle(runner, work):
    """Short non-preempting admission, independent of the ordinary Wander switch."""
    if not available():
        return {'status': 'unavailable'}
    import body_admission as focus
    from . import game_chat_gate
    from .runtime_models import (WanderRun, WanderActivity, RunState, ActivityState, GoalMode, SettlementReason)
    from .event_types import EventType
    runner.initialize()
    if runner._tick_lock.locked() or runner.store.get_active_run() or game_chat_gate.status()['busy']:
        return {'status': 'busy'}
    try:
        owner = focus.reserve(require_idle=True, direction='xhs')
    except RuntimeError:
        return {'status': 'busy'}
    try:
        with focus.owned(owner):
            async with runner._tick_lock:
                if runner.store.get_active_run():
                    return {'status': 'busy'}
                bundle = await runner.context_provider()
                sid = bundle.decision.session_id
                if not sid or not bundle.decision.persona.strip():
                    return {'status': 'context_unavailable'}
                if sid != work.get('session_id'):
                    return {'status': 'session_changed'}
                from xhs_publish.store import account_config
                if account_config()['account_id'] != work.get('account_id'):
                    return {'status': 'account_changed'}
                # A runtime attempt is not the domain model/reply identity. A cancelled
                # attempt may resume the same safe domain stage without reopening it.
                prefix = f'{sid}:{work["admission_key"]}'
                index = 0
                while True:
                    run_id = hashlib.sha256(f'xhs-idle:{prefix}:{index}'.encode()).hexdigest()[:32]
                    previous = runner.store.get_run(run_id)
                    if previous is None:
                        break
                    if previous['state'] not in {'completed', 'aborted', 'interrupted'}:
                        return {'status': 'already_running'}
                    index += 1
                run = WanderRun(run_id=run_id, session_id=sid, trigger_reason=TRIGGER,
                    state=RunState.ACTIVE_WAIT, context_snapshot={'xhs_interaction_work': work})
                activity = WanderActivity(run_id=run_id, activity_type=EventType.CHECK_XIAOHONGSHU_INTERACTIONS.value,
                    state=ActivityState.ACTIVE_WAIT, goal_mode=GoalMode.SINGLE, goal_value=1,
                    reason='空闲时查看实际收到的小红书评论与艾特，由AI决定回复或离开。',
                    next_node_at=runner.clock().isoformat())
                runner.store.save_plan(run, [activity])
                runner.store.record_delivery(run_id=run_id, activity_id=activity.activity_id,
                    delivery_type=TRIGGER, status='not_requested')
                node = runner.controller.begin_node(activity.activity_id)
                node.source_payload = {'xhs_interaction_ids': work['interaction_ids']}
                runner.store.save_node(node)
                cancelled = False
                try:
                    execution = await runner.executor.execute(node.node_id, decision_context=bundle.decision)
                    payload = execution.payload or {}
                except asyncio.CancelledError:
                    cancelled = True
                    payload = recover_node(runner.store, runner.store.get_node(node.node_id))
                except Exception:
                    payload = recover_node(runner.store, runner.store.get_node(node.node_id))
                good = payload.get('status') == 'completed'
                runner.controller.complete_node_review(node.node_id, reflection=payload.get('reflection', ''),
                    continue_activity=False, execution_failed=not good,
                    abort_reason='user_interrupt' if cancelled else '' if good else payload.get('status', 'interaction_incomplete'))
                if runner.share_adapter:
                    runner.share_adapter.prepare_activity_event(activity.activity_id)
                runner.controller.settle_activity(activity.activity_id,
                    SettlementReason.USER_INTERRUPT if cancelled else
                    SettlementReason.NATURAL_STOP if good else SettlementReason.EXECUTION_ERROR,
                    summary=summary(runner.store.list_nodes(activity.activity_id)), share_decision=False)
                if runner.share_adapter:
                    await runner.share_adapter.record_activity(activity.activity_id)
                if cancelled:
                    raise asyncio.CancelledError
                return {'status': 'completed' if good else 'incomplete', 'run_id': run_id,
                        'activity_id': activity.activity_id, 'result': payload}
    finally:
        focus.release(owner)


class InteractionWatcher:
    """Signals request a native scan; only the durable inbox supplies work."""
    def __init__(self, runner, *, store=None, signals=None, scanner=None, enabled=available, idle=None,
                 clock=time.monotonic, fallback_seconds=300):
        self.runner, self.store, self.signals = runner, store, signals
        self.scanner, self.enabled, self.idle = scanner, enabled, idle
        self.clock, self.fallback_seconds = clock, max(30, fallback_seconds)
        self.next_scan_at = 0
        self.last_signal_key = None

    async def poll(self):
        if not self.enabled():
            return {'status': 'unavailable'}
        if self.idle is not None and self.idle() < 30:
            return {'status': 'busy'}
        # Do not acquire phone/UI work while the existing activity or chat owns attention.
        import body_admission as focus
        from . import game_chat_gate
        if (self.runner._tick_lock.locked() or self.runner.store.get_active_run()
                or focus.busy() or focus.status()['ordinary_work_count'] or game_chat_gate.status()['busy']):
            return {'status': 'busy'}
        from xhs_interactions.store import InteractionStore
        from xhs_interactions.signals import SignalReader
        from xhs_interactions.service import request
        from xhs_publish.store import account_config
        self.store = self.store or InteractionStore()
        self.signals = self.signals or SignalReader()
        bundle = await self.runner.context_provider()
        sid = bundle.decision.session_id
        if not sid:
            return {'status': 'context_unavailable'}
        account = account_config()
        signal = None
        try:
            signal = await self.signals.snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A lost signal connection cannot delete already-persisted inbox work.
            logger.debug('XHS inbox refresh unavailable; durable pending retained')
        signalled = bool(signal and signal['availability'] == 'connected'
                         and (signal['signals'] or signal['needs_full_scan']))
        signal_key = (json.dumps([sorted(row['id'] for row in signal['signals']),
                                 signal['full_scan_token'] if signal['needs_full_scan'] else ''])
                      if signalled else None)
        if (signalled and signal_key != self.last_signal_key) or self.clock() >= self.next_scan_at:
            # Foreground XHS may omit OS notifications. A bounded idle scan is
            # the fallback; its empty result never dispatches a AI/model decision.
            self.next_scan_at = self.clock() + self.fallback_seconds
            if signalled:
                self.last_signal_key = signal_key
            try:
                observed = await (self.scanner or request)(session_id=sid, action='scan', store=self.store)
                if (signal and observed.get('status') == 'observed'
                        and observed.get('coverage') == 'since_cutoff'):
                    await self.signals.acknowledge([row['id'] for row in signal['signals']],
                        full_scan_token=signal['full_scan_token'] if signal['needs_full_scan'] else '')
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug('XHS app scan unavailable; inbox work and full-scan signal retained')
        work = select_work(self.store, sid, account['account_id'])
        if not work:
            return {'status': 'empty'}
        return await self.runner.execute_idle_xhs_interactions(work)

    async def run(self):
        while True:
            try:
                await self.poll()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('XHS interaction watcher unavailable; pending retained')
            await asyncio.sleep(30)
