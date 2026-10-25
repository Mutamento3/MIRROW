"""A ready work becomes AI's actual message; system receipts stay system facts."""
import asyncio
import json
import logging
import time
from body_admission import ordinary

logger = logging.getLogger(__name__)


@ordinary
async def compose(service, job, receipt, session_id):
    from .autonomy import build_messages
    from .visit_material import current_room
    from .sharing import card
    from .presentation import sharing_material, entries
    from wander_manager.flash_structured import call_flash_json_detailed
    from context_builder.channel_projection import prepare
    from llm_client import (DEEPSEEK_FLASH_API_KEY, DEEPSEEK_API_KEY,
        DEEPSEEK_FLASH_API_URL, DEEPSEEK_API_URL, DEEPSEEK_FLASH_MODEL)
    material = {'source': '工作坊实际公开交付；不是人类伙伴的新消息',
                'current_room': current_room(service, job['id']),
                'delivery': card(service, job, receipt), 'image_input_artifact_ids': []}
    material['current_delivery_files'] = sharing_material(service, receipt['body']['artifacts'])
    images = []
    for item in receipt['body']['artifacts']:
        if item['kind'] == 'image' and len(images) < 4:
            from .image_review import input_image
            artifact = service.artifact(item['id'])
            pixels, evidence = input_image(service.artifact_path(artifact), artifact['sha256'])
            images.append(pixels['url'])
            material['image_input_artifact_ids'].append(item['id'])
            material.setdefault('image_input_evidence', []).append({'artifact_id': item['id'], **evidence})
    messages = await build_messages(session_id, material)
    messages.append({'role': 'system', 'content': (
        '【本轮输出合同】你是 AI，这份公开作品已经制作出来，现在把它递给人类伙伴。'
        '依据真实需求、合作经过、成品及你与人类伙伴的关系，说一条你自己想对她说的话。'
        '正文是聊天赠言，成品将直接附在你的消息下面，可以自然提到想送它的缘由或邀请她打开。'
        '本次分享对象由 current_delivery_files 给出，围绕这些真实文件与当前工单表达；历史里其他作品的赠言只是旧对话。'
        '文件静态文字与实际图片可以作为创作依据；原始需求里尚未交付的页面、赠言排版或功能仍只是愿望。'
        '图片输入对应 image_input_artifact_ids；其他文件只有记录，没有实际运行证据时不声称亲自体验过。'
        '交付与验收是两回事；本轮只分享，不重新讨论或确认工单。'
        '只输出 JSON {"message":"你对人类伙伴说的最终话语"}，不输出执行汇报、系统标签或工具调用。')})
    messages, _ = prepare(messages)
    result = await call_flash_json_detailed(messages, image_inputs=images or None,
        enable_thinking=False, temperature=0.7, route_label='workshop_gift_message',
        api_key=DEEPSEEK_FLASH_API_KEY or DEEPSEEK_API_KEY,
        api_url=DEEPSEEK_FLASH_API_URL or DEEPSEEK_API_URL, model_name=DEEPSEEK_FLASH_MODEL)
    if result.status != 'ok' or not isinstance(result.parsed, dict):
        raise RuntimeError('K_gift_message_unavailable')
    text = result.parsed.get('message')
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError('empty_K_gift_message')
    return text.strip(), material['image_input_artifact_ids']


async def prepare_share(service, receipt):
    from shared_state import get_active_session_id
    from .sharing import share
    key = 'gift-message:' + receipt['id']
    with service.store.db() as db:
        old = db.execute('SELECT result FROM commands WHERE key=?', (key,)).fetchone()
        shared = [json.loads(row[0]) for row in db.execute(
            "SELECT body FROM receipts WHERE job_id=? AND kind='artifact_shared'", (receipt['job_id'],))]
    if old:
        return json.loads(old[0])
    artifact_ids = {item['id'] for item in receipt['body']['artifacts']}
    from .presentation import entries
    if not entries([service.artifact(item['id']) for item in receipt['body']['artifacts']]):
        return {'supporting_files_only': True}
    if any(row.get('source') == 'k' and {item['id'] for item in row.get('artifacts', [])} == artifact_ids for row in shared):
        return {'already_shared_by_k': True}
    job = service.store.job(receipt['job_id'], include_deleted=True)
    if job['deleted'] or job.get('visibility') != 'public' or job['revision'] != receipt['body']['revision']:
        return {'superseded': True}
    session_id = get_active_session_id()
    if not session_id:
        raise RuntimeError('K_session_unavailable')
    text, viewed = await compose(service, job, receipt, session_id)
    # Recheck after the model turn: a new revision is a different work.
    current = service.store.job(job['id'], include_deleted=True)
    if current['deleted'] or current['revision'] != job['revision']:
        return {'superseded': True}
    result = share(service, current, [service.artifact(item['id']) for item in receipt['body']['artifacts']],
                   source='k', text=text, delivery_key=key, session_id=session_id)
    for artifact_id in viewed:
        service.store.event(job['id'], 'K_received_visual', {'artifact_id': artifact_id,
            'revision': job['revision'], 'source': '分享赠言时实际模型图片输入'})
    return result


async def _prepare(service, receipt):
    try:
        await prepare_share(service, receipt)
        service.store.acknowledge([receipt['id']], projection=True)
        service.store.acknowledge([receipt['id']])
    except asyncio.CancelledError:
        raise
    except Exception:
        service.gift_retry_at = time.monotonic() + 300
        logger.exception('Workshop gift message pending; receipt=%s', receipt['id'])
    finally:
        service.wake.set()


def schedule(service, receipt):
    from .residency_store import automatic_delivery_allowed, kept_current
    if kept_current(service,receipt['job_id']):
        # This is only a system gift receipt. Explicit AI shares use deliver().
        service.store.acknowledge([receipt['id']],projection=True)
        service.store.acknowledge([receipt['id']])
        return
    if not automatic_delivery_allowed(service,receipt['job_id']):return
    if not service.share_sender or not service.store.settings()['enabled'] or time.monotonic() < service.gift_retry_at:
        return
    if service.gift_task is None or service.gift_task.done():
        service.gift_task = asyncio.create_task(_prepare(service, receipt))


async def deliver(service, receipt, card):
    if not service.share_sender:
        return False
    return bool(await service.share_sender({
        'event_id': 'workshop_share_' + receipt['id'],
        'event_type': 'workshop_share_followup', 'activity_source': 'workshop',
        'session_id': receipt['body'].get('session_id') or '',
        'message': card.get('text') or '', 'plain_assistant': True,
        'replace_existing': True,
        'tool_calls': [{'tool': 'workshop', 'arguments': {}, 'result': 'AI 分享作品',
                        'extra_data': {'workshop': card}}],
    }))
