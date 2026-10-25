"""Durable UI invitation, bounded AI visit and frozen event outbox.

An interrupted visit is reported after restart, never automatically replayed.
The existing workshop command/receipt tables own its records.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from .service import get_service
from .store import now
from .ui_action import CONTENT, EVENT_TYPE, invitation_fact

logger = logging.getLogger(__name__)
_task = None
_reserved = False
KINDS = {'visit_started', 'visit_finished', 'visit_failed', 'visit_interrupted'}


def busy():
    return _reserved


def status(service=None):
    service = service or get_service()
    with service.store.db() as db:
        row = db.execute("SELECT result FROM commands WHERE key LIKE 'invitation:%' ORDER BY rowid DESC LIMIT 1").fetchone()
    if not row:
        return None
    record = json.loads(row[0])
    result = {key: record.get(key) for key in ('id', 'state', 'summary', 'started_at', 'finished_at', 'job_id')}
    if _private_ref(service, result.get('job_id')):
        result['job_id'] = ''
    return result


def _event(service, record, kind):
    # The command phase and its frozen receipt commit together.
    with service.store.db() as db:
        db.execute('INSERT OR REPLACE INTO commands VALUES(?,?)',
                   ('invitation:' + record['id'], json.dumps(record, ensure_ascii=False)))
        service.store._event(db, '', kind, dict(record))
    service.wake.set()


def recover(service):
    with service.store.db() as db:
        rows = db.execute("SELECT result FROM commands WHERE key LIKE 'invitation:%'").fetchall()
    for row in rows:
        record = json.loads(row[0])
        if record['state'] == 'visiting':
            record.update(state='interrupted', finished_at=now(),
                          summary='这次工作坊来访被服务重启中断；已保存的工单与行动保留，可以再次邀请 AI 查看。')
            _event(service, record, 'visit_interrupted')


def _private_ref(service, job_id):
    if not job_id:
        return False
    from .privacy import private
    try:
        return private(service.store.job(job_id))
    except KeyError:
        return True


def card(receipt, service=None):
    record = dict(receipt['body'])
    service = service or get_service()
    if _private_ref(service, record.get('job_id')):
        record['job_id'] = ''
    record['actions'] = [{**row, 'job_id': ''} if _private_ref(service, row.get('job_id')) else row for row in record.get('actions') or []]
    return {'kind': receipt['kind'], 'title': 'AI 的工作坊来访', 'timestamp': receipt['created_at'],
            **{key: record.get(key) for key in ('id', 'job_id', 'summary', 'source',
                                               'invitation_message_id', 'actions', 'started_at', 'finished_at', 'experience')}}


async def invite(template_id=None, text=None, *, continuous=False, job_id=None):
    global _reserved, _task
    from . import autonomy
    import body_admission as focus
    from wander_manager import game_chat_gate
    from shared_state import get_latest_persona_prompt
    from .creative_templates import invitation_content, chat_invitation
    content = invitation_content(template_id, CONTENT, text)
    service = get_service()
    if continuous:
        from .residency_store import records, ACTIVE
        if any(row['state'] in ACTIVE for row in records(service)):
            raise RuntimeError('这次合作仍在继续，可直接查看进度；无需重复邀请')
    if _reserved or autonomy._visiting:
        raise RuntimeError('AI 正在工作坊里，请等这次来访结束')
    if not service.store.settings()['enabled']:
        raise RuntimeError('工作坊尚未开放，请先在设置中开启')
    if not service.sender or not get_latest_persona_prompt():
        raise RuntimeError('AI 的会话或工作坊消息通道尚未就绪')
    if game_chat_gate.status()['busy']:
        raise RuntimeError('AI 正在玩游戏，请等游玩结束后再邀请')
    if focus.status()['ordinary_work_count']:
        raise RuntimeError('AI 正在完成当前回复或活动，请稍后再邀请')
    lease = focus.reserve(require_idle=True, direction='workshop')
    _reserved = True
    try:
        from ui_action_messages import invite_action
        invitation = await invite_action(chat_invitation(CONTENT, text), EVENT_TYPE, 'visit_workshop')
        record = {**invitation, 'id': invitation['invitation_message_id'], 'state': 'visiting',
                  'started_at': now(), 'finished_at': '', 'job_id': job_id or '', 'actions': [],
                  'summary': '人类伙伴的快捷邀请已保存，AI 正在来访工作坊。',
                  'invitation_content': content, 'template_id': template_id, 'invitation_text':text}
        if continuous:
            record.update(state='collaborating', summary='合作已开始；等待制作时聊天仍然可用。')
        _event(service, record, 'visit_started')
        if continuous:
            from .residency_store import attach, status as collaboration_status
            from .residency import schedule
            attach(service, record['id'], {
                'session_id': record['session_id'], 'source':'ui_action', 'job_id':job_id,
                'invitation_message_id':record['id'], 'template_id':template_id,
                'request_text':text, 'creative_request':content,
                'source_fact':invitation_fact(chat_invitation(CONTENT,text), record['started_at'])})
            _reserved = False
            focus.release(lease)
            schedule(service)
            return {'started':True, 'invitation_message_id':record['id'], 'visit':status(service),
                    'collaboration':collaboration_status(service)}
        _task = asyncio.create_task(_run(service, record, lease))
        return {'started': True, 'invitation_message_id': record['id'], 'visit': status(service)}
    except BaseException:
        _reserved = False
        focus.release(lease)
        raise


async def _run(service, record, lease):
    global _reserved
    import body_admission as focus
    from .autonomy import visit
    kind = 'visit_failed'
    try:
        with focus.owned(lease):
            outcome = await visit(record['id'], invocation={
                'session_id': record['session_id'], 'source': 'ui_action',
                'invitation_message_id': record['id'],
                'template_id': record.get('template_id'),
                'request_text': record.get('invitation_text'),
                'creative_request': record.get('invitation_content') or CONTENT,
                'source_fact': invitation_fact(CONTENT + ('\n'+record['invitation_text'].strip() if record.get('invitation_text') else ''), record['started_at'])})
        if outcome.get('status') != 'success':
            raise RuntimeError('visit_unavailable')
        actions = outcome.get('actions') or []
        labels = {'publish': '公开工单', 'create': '提交工单', 'message': '补充讨论', 'edit': '修改工单', 'confirm': '确认方案', 'answer':'回答工具手问题',
                  'cancel': '取消工单', 'delete': '删除工单', 'inspect': '查看作品', 'accept': '验收作品',
                  'pause': '暂停工单', 'resume': '恢复排队', 'reconcile': '核对现场', 'list': '查看清单', 'room': '查看房间'}
        confirmation_labels={'already_confirmed':'查看已有方案确认','answer_required':'查看待答问题，尚未回答'}
        summary = ('AI 完成这次工作坊来访：' + '、'.join('查看已保存的问题答复' if row.get('decision_status')=='already_answered' else confirmation_labels.get(row.get('confirmation_status'),labels.get(row['action'], '查看工作坊')) for row in actions) + '。'
                   if actions else 'AI 结束了这次工作坊来访，本次没有提交新的工单或行动。')
        waits = [row.get('wait_status') for row in actions if row.get('wait_status')]
        if waits:
            summary += '本次曾短等执行器回复。' if 'ready' in waits else '本次短等因超时或排队结束，进展留待后续来访。'
        record.update(state='finished', actions=actions, job_id=outcome.get('job_id', ''), summary=summary)
        if outcome.get('experience') is not None:
            record['experience'] = outcome['experience']
        kind = 'visit_finished'
    except asyncio.CancelledError:
        record.update(state='interrupted', summary='这次工作坊来访被服务停止中断；已保存的工单与行动保留。')
        kind = 'visit_interrupted'
        raise
    except Exception as exc:
        logger.exception('工作坊快捷来访未完成: visit_id=%s', record['id'])
        record.update(state='failed', summary=('这次创作邀请尚未提交对应工单；没有替 AI 编造需求，请重试邀请。' if str(exc)=='invited_request_not_submitted' else '这次工作坊来访未完成；已保存的工单与行动保留，可以再次邀请 AI 查看。'))
    finally:
        record['finished_at'] = now()
        try:
            _event(service, record, kind)
        finally:
            _reserved = False
            focus.release(lease)


async def close():
    if _task and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
