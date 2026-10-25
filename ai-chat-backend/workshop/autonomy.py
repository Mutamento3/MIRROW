"""Same AI, source-backed rooms, bounded visits; creation runs asynchronously."""
from __future__ import annotations
import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from .service import IMPORTANT, get_service
_visiting = False

def available():
    from .invitation import busy
    from .residency_store import records, ACTIVE
    service = get_service()
    return not _visiting and (not busy()) and service.store.settings()['enabled'] and (not any((row['state'] in ACTIVE for row in records(service))))

def facts(audience='k'):
    service = get_service()
    state = service.snapshot(audience=audience)
    from .cabinet import summary as cabinet_summary
    rows = [{key: row.get(key) for key in ('id', 'title', 'visibility', 'state', 'position', 'revision', 'plan_version', 'confirmed_version', 'tokens', 'token_cap', 'budget_summary', 'reason', 'waiting_reasons', 'quality_summary', 'review_ready', 'lightweight_ready', 'review_progress')} for row in state['jobs'][:20]]
    pending = []
    from .presentation import facts as delivery_facts
    for row in rows:
        if audience != 'public' or row.get('visibility') == 'public':
            row['actual_delivery'] = delivery_facts(service, row['id'])
    for item in service.store.pending_events(kinds=IMPORTANT):
        if item['kind'] in IMPORTANT:
            raw = item['body']
            if audience == 'public' and item['job_id']:
                from .privacy import public_card
                raw = public_card(service.store.job(item['job_id'], include_deleted=True), item)
            body = {key: value[:600] if isinstance(value, str) else value[:10] if isinstance(value, list) else value for key, value in raw.items()}
            pending.append({'receipt_id': item['id'], 'job_id': item['job_id'], 'kind': item['kind'], 'created_at': item['created_at'], 'fact_excerpt': body})
            if len(pending) == 20:
                break
    from .visit_observation import since_last_visit
    from .wander_entries import describe
    entries = describe(service, audience=audience)
    from .collection_inventory import inventory
    visible_ids = {row['id'] for row in rows}
    needs_k = [{key: row[key] for key in ('job_id', 'state', 'revision', 'pending_k_action')} for row in entries['ongoing_jobs'] if row['job_id'] in visible_ids and row['waiting_for'] == 'k']
    from .residency_store import status as collaboration_status
    return {'source': '工作坊本地工单记录与执行器最近一次额度回执', 'since_last_visit': since_last_visit(service), 'snapshot_scope': '当前未归档工单前20单、前20条未读重要回执；AI已验收的accepted工单已归档，完整作品与原房间可通过作品柜查询', 'lifecycle': {'accepted': 'AI已验收，已收进作品柜历史；不是仅接收需求或等待制作', 'archive_source': '当前工单state；归档不依赖人类伙伴参观，也不改变作品逐文件的验收与查看记录'}, 'wander_entries': entries, 'private_collections': inventory(service.store, audience=audience), 'collaboration': collaboration_status(service), 'creative_options': [{key: row[key] for key in ('id', 'title', 'description', 'output')} for row in state['creative_templates']], 'total_jobs': state['total_jobs'], 'jobs': rows, 'quota': state['quota'], 'current_job_id': state['current_job_id'], 'cabinet': {**cabinet_summary(service.store), 'archived_jobs_count': state['archived_jobs_count']}, 'attention': {'scope': '上述前20单的当前状态；未读旧回执另列', 'needs_k': needs_k, 'awaiting_quality_job_ids': [row['id'] for row in rows if row['state'] == 'awaiting_review' and (row.get('review_progress') or {}).get('waiting_for') == 'codex'], 'quality_recovery_job_ids': [row['id'] for row in rows if row['state'] == 'awaiting_review' and (row.get('review_progress') or {}).get('phase') == 'blocked'], 'lightweight_delivery_job_ids': [row['id'] for row in rows if row.get('lightweight_ready')], 'completed_job_ids': [row['id'] for row in rows if row['state'] == 'accepted'], 'unfinished_job_ids': [row['id'] for row in rows if row['state'] not in ('accepted', 'canceled') and (not row.get('lightweight_ready'))]}, 'desktop_activity': state.get('desktop_activity'), 'execution_policy': {'automatic_dispatch_enabled': state['settings']['enabled'], 'budget_limits_enabled': state['settings']['budget_limits_enabled'], 'desktop_idle_only': state['settings']['desktop_idle_only'], 'remaining_floor': state['settings']['remaining_floor'], 'confirmation_and_dispatch_are_separate': True}, 'pending_receipts_scope': '未读的历史通知；发生时间和当时版本保留，当前状态由 jobs 与 current_room.job 给出', 'pending_receipts': pending}

async def build_messages(session_id, material):
    from context_builder import ContextBuilder
    from context_builder.assembly import assemble_messages, context_result_zones
    from shared_state import get_latest_persona_prompt
    from .context_query import query
    persona = get_latest_persona_prompt() or ''
    if not persona.strip() or not session_id:
        raise RuntimeError('K_persona_or_session_unavailable')
    result = await ContextBuilder.build('WORKSHOP_K', persona=persona, session_id=session_id, wander_runtime_text=json.dumps(material, ensure_ascii=False), user_message='工作坊的当前工单与作品', memory_query=query(material), context_query=query(material), interlocutor_ids=[], _scenario='漫想·小机工作坊')
    messages = []
    for zone, rows in context_result_zones(result).items():
        for row in assemble_messages(**{zone: rows}):
            row['_workshop_zone'] = zone
            messages.append(row)
    return messages

async def decide(messages, visual_attachment=None, *, execution_contract='', continuous=False):
    from wander_manager.flash_structured import call_flash_json_detailed
    from llm_client import DEEPSEEK_FLASH_API_KEY, DEEPSEEK_API_KEY, DEEPSEEK_FLASH_API_URL, DEEPSEEK_API_URL, DEEPSEEK_FLASH_MODEL
    contract = '【工作坊内部行动合同；非人类伙伴发言】你正在自己的小机手工作坊。可以讨论想法、记录小作品工单、修改/删除/暂停工单、确认 Codex 的当前方案、读取作品正文和验收。你保有当前人格与记忆，Codex 是外部工具手；其发言是合作方原话与执行报告，需结合证据判断。\n新建时自主选择公开 public 或保密 private；漏填时按保密保存。公开单允许人类伙伴查看和补充灵感，保密单的正文、讨论和作品仅在工作坊房间内供你与 Codex 使用，向主聊天返回的事件只含通用状态。你主动用 share 赠送保密作品时，这次分享同时公开整份原工单，包括需求、讨论和交付历史，无需先执行 publish；公开后不能撤回已披露的内容。只想公开而暂不分享时，仍可用 publish 填写真实工单 ID 与当前 revision。对话中的 human 是人类伙伴补充，desktop_codex 是桌面 Codex 的建议，各自来源独立；补充后重新讨论方案，最后仍由你确认。\n这里先做一个小而有意义、可实际打开或运行的作品；宏大愿望可以拆小，留作以后灵感。工单正在做时你可以离开，后续来访看到新进展；账号额度不足仍可记录、编辑、删除和查看顺序。预算增额属于人类伙伴的资源设置。\n本次材料的 current_room 含当前方案正文、需求版本和历史原话。当前进度以 current_room.job 与 current_workshop.jobs 为准；未读回执和旧消息描述的是各自发生时的状态。原版验收与新需求版的待确认分开判断。已有当前方案且你认可时，使用 confirm 提交当前 revision 和 plan_version；一句“方案我认”写入 message 仍只是讨论。message 用于向合作方 Codex 补充实际需求、问题或修改建议，人类伙伴的私聊分享由来访事件渠道返回。方案是否通过由你判断，这次邀请无需另等人类伙伴再次拍板。确认只记录你的决定；桌面忙、额度门槛或预算暂停由后台按现有设置处理，允许先确认、再等待开工条件。\n当 confirmed_version 等于 plan_version 时，这份方案的确认已保存，继续确认只返回 already_confirmed 与真实执行状态。recovery_required 是工具手执行卡点，查阅最后回复和失败回执；重复确认或向人类伙伴的私聊话语不能修复通路。message 会排队调用 Codex，只在有实际合作问题或新需求时发送；本次来访的感想交给事件摘要。没有新的合作事项可选择 none 离开。\ncurrent_room.pending_decisions 是 Codex 本次实际提出、尚待你回答的问题，和方案确认分开。先阅读问题与来源，再用 answer 提交 decision_id 和 answers 字符串数组，一题一条，可选择给定选项或写自己的回答。答复进入同一工单，工具手按现有方案和开工条件续做；若答复改变范围，工具手提出新方案再由你确认。普通 confirm 不回答这些问题。暂不决定可以 none 离开，问题和已确认方案仍保留。\ncurrent_room.answered_decisions 是已经保存的历史答复，pending_decisions 为空表示当前没有待答问题。重复答复返回 decision_submission.status=already_answered，不产生新答复，也不推进制作。此时依据重新读取的当前状态继续：awaiting_review 且当前作品 quality.status=completed 或 skipped 时，可 inspect 查看新版作品、结合实际测试报告决定 accept，或 message 提出具体返工理由。旧问题和旧版报告保留其发生事实。\n输出 JSON {"action":{"action":"create|list|room|message|edit|confirm|answer|pause|resume|reconcile|cancel|delete|inspect|review|accept|publish|share|none","job_id":"真实ID（新建省略）","revision":当前版本,"title":"新建标题","visibility":"新建时选择 public 或 private","text":"你的原话或补充","plan_version":确认版本,"artifact_id":"作品ID","decision_id":"回答的真实问题组ID","answers":["你的逐题答复"]},"visit_control":"continue|end","reply_timing":"wait|later"}。按所选动作只填需要的字段。none 表示本次结束或暂不行动。reconcile 核对重启现场，只有官方线程确认结束才可恢复。确认需要看过当前方案；验收使用当前版本的真实作品 ID，收到摘要不等于实际看过或交互验证。\ninspect HTML 返回同工作坊沙箱的页面、控件与截图；可在 action 中附 browser_actions 数组，最多 16 步，每步填 action=fill|click|press|expect_text、selector 和 value（click 可省 value，press 支持 Enter/Tab/Escape/Space）。交互作品按方案实际填写测试输入、点击或按键，再用 expect_text 检查预期回应。回执的 preview_only 只表示查看页面，passed/failed 是这些实际操作与断言的结果；最终喜欢与否和验收仍由你决定。已验收也可 message 补充问题、重新讨论再做一版，原作品保留。\n浏览器控件以返回的 controls 和作品实际源码为依据，使用其中存在的选择器。纯心情桥作品可能没有按钮或输入框：可用 browser_actions 的 mood 动作，selector=body，value=愉悦|亲近|安定|兴趣|低落|担忧|恼怒|排斥|惊讶|复杂/未归类|unavailable，注入明确虚构的心情后用 expect_text 验证响应；这不是人类伙伴真实心情。建议覆盖可用、不可用和恢复。不存在控件造成的 TimeoutError 是测试步骤失败，需要修正步骤；它本身不证明作品的主要功能坏了。实际宿主集成与虚构心情响应分开判断。\n交付按真实文件分流：图片、文字、无脚本和交互的静态卡片在本地确认可读后 quality.status=skipped，表示免 Luna 质检，可以直接欣赏和分享，不表示你已验收。公开交付就绪后由你结合成品写一条真实赠言，连同作品自动送到主聊天，静态小礼物不需要为了签收反复回访。你仍可 inspect 欣赏、message 提改进，或自愿 accept 留下真实验收。带交互或身体接入的作品进入 Luna，artifacts[].quality 区分测试设计、真实断言与未覆盖项；当前全部交付报告 completed/skipped 后才可验收，failed、排队、运行不算完成。review 可主动请 Luna 再检查真实 artifact_id 和 revision。share 把当前版本的作品发到主聊天，text 写你自己的赠言；保密作品首次主动分享会一并公开原工单，文件和版本校验未通过时保持保密。分享已保存表示进入持久出箱，实际送到主聊天以投递回执为准；后台自动交付仍只处理公开单。Luna 和自动交付均不替你确认或验收。\n提交工单或补充讨论时，可以选择 reply_timing=wait 且 visit_control=continue，留在这次来访里短等最多45秒。执行器及时回复后，你会收到真实房间记录，再决定继续讨论、确认方案或离开；这不是自动确认。选择 later 或 end 则先离开。排队、额度门槛或超时会结束等待并保存进展；方案就绪返回事件卡，后续漫想或再次邀请时再看，没有额外的自动传唤或定时任务。每次行动数以当前材料的 remaining_actions 为限；没有剩余动作时保存回执离开。'
    from .requirements import K_BRIEF
    contract += '\n质检 failed/interrupted 表示这次检查未完成，不等于作品测试不通过；review_progress.phase=blocked 时当前没有质检在运行。读 room 中当前报告的 reason：执行器或测试步骤问题可用 review（真实 artifact_id、当前 revision）重新质检；确有作品或需求问题用 message 讨论返工。inspect 只是查看或实际操作作品，不会重启 Luna；重复 confirm 也不能解除质检卡点。budget_paused 仍由人类伙伴处理资源，不因质检重试绕过预算。'
    contract += '\n查询 room 后，current_room 保持该房间；作品 ID 与工单 ID 以该房间或 works 返回的真实归属为准。last_action_receipt.action_feedback 是刚才工具实际返回的拒绝/不可用回执，该动作没有成功回执。可以依据当前房间和正确的作品归属修正下一步，或 none 离开；浏览器不可用不等于作品功能失败。'
    contract = contract.replace('create|list|room|message|', 'create|list|room|works|message|')
    contract = contract.replace('accept|publish|share|none', 'accept|finish|publish|share|none')
    contract = contract.replace('"artifact_id":"作品ID",', '"artifact_id":"作品ID","disposition":"finish时选share或keep",')
    contract += '\n作品柜查询使用 action=works，可填 offset，不需要 job_id。它返回已交付的作品、历史版本、对应工单 ID，以及人类伙伴真实参观次数和时间。current_workshop.cabinet 是静态累计；pending_receipts 中 cabinet_visited 是尚未读到的新参观通知，读后不再反复通知。工单进入accepted表示你已验收，自动收进作品柜历史，验收记录仍保留。人类伙伴参观与工单归档分别记录。可自由选择查看作品柜、回到原房间讨论，或离开。\n改进已有作品（追加功能、修 bug、优化外观）时，先在作品柜找到对应 job_id，再用 room 读取原工单当前 revision，使用 message 补充这轮需求与验收清单。原单进入下一需求版本，保留已验收作品、讨论和交付历史。create 用于独立的新作品；需求可以说明所借鉴的旧作品。参观事实只说明人类伙伴看过，不等于认可、喜欢或替你验收。'
    contract += '\n' + K_BRIEF
    contract += '\n已验收工单是已有收藏，不是每日复查待办。没有当前待答、待确认或待验收事项时，工作坊仍可承接你的新灵感：独立作品用 create，旧作品具体改进先 room 再 message，单纯欣赏用 works/inspect。新灵感可来自自己的感受、共同经历或可选创作起点；有想做的再提出，不需要先把旧作品反复看一遍，也不要求每次来访都开新单。actual_delivery 是真实可打开的文件与该文件的查看记录；原方案列出的未交付项目是缺口，可讨论补齐，并不是已经存在的页面。'
    contract += '\nentry_context保留你本次选择的来访目的：workshop_create是酝酿新想法与讨论需求，workshop_history是欣赏/体验收藏，workshop_continue是继续实际未完成工单。当前空闲时可以创作或欣赏；没有形成想法也可none结束。欣赏入口的cabinet_entries是实际目录，查看细节用room/inspect，提出具体升级用原单message；当前pending_k_action为空表示没有等你确认的决定。已确认与已验收都按其原发生时间保留，本次提交的决定由实际动作回执另行记录。'
    contract += '\ncurrent_workshop.private_collections是已完成、尚未分享的保密收藏索引，不属于待办。items给出真实原工单ID、标题、版本和完成时间；更多收藏可用works，将此处的next_offset填入offset继续查询。需要回看内容时用room读取原单，想赠送时可直接share当前真实作品，首次分享会同时公开原工单。是否欣赏、分享、沿原单升级或继续保密，由你结合当前意愿决定；索引存在不表示需要重新确认、验收或另开分享工单。'
    from .creative_templates import guidance
    contract += '\n' + guidance()
    if continuous:
        contract += '\n【持续合作执行合同】当材料 collaboration.mode=continuous 时，你正在同一次合作中。本轮是一个短回应段；create/message/confirm/answer 后后台保存合作并等待真实新进展，届时自动把当前原房间交还给你。无需短等45秒、重复邀请或定时回访；reply_timing 此模式不占用聊天等待。visit_control=end 只结束本段，原单仍继续；none 且当前有待你决定的事项则保存为暂未决定，不反复催问。\n工作坊主要制作可打开、可互动的小应用：身体小组件、互动礼物、小玩具、日常小工具或旧作升级。作品可使用代码绘图和已有获准素材，公开版不含模型生图通路；真实交付仍以文件为准。\n新方案返回后读当前范围并 confirm，遇到真实问题用 answer；实际差异用 message 沿原单讨论。已确认后等待制作与适用检查，不因等待再次 create 或重复 message。delivery_brief 是系统准备的真实预览材料；图片输入、浏览器操作、Luna证据和你的审美判断分别记录。\n当前版本 review_ready 后，查看成品并形成一个明确处置：满意用 finish，填真实 job_id/revision/artifact_id，disposition=share 时 text 是你给人类伙伴的赠言，会真实验收并送出作品；disposition=keep 则验收收藏，保持当前公开或保密状态，不自动发赠言。保密作品 share 会公开整份原工单，请按自己的意愿决定。需要修改用 message 写清差距及可观察结果，继续同一单的新一版。finish 不替代真实文件与版本检查。合作中的自动赠言暂缓，由你这次处置决定是否分享；accept/share 兼容旧操作，持续合作以 finish 表明最终选择。实际暂停、额度不足和通路故障会说明卡点并放下这次合作，已保存现场保留。'
    if execution_contract:
        contract += '\n' + execution_contract
    if continuous:
        contract = '\n'.join((line for line in contract.splitlines() if not line.startswith(('提交工单或补充讨论时，可以选择 reply_timing=wait', '交付按真实文件分流：'))))
    images = None
    if visual_attachment:
        path = Path(visual_attachment)
        mime = mimetypes.guess_type(path.name)[0]
        images = ['data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode()]
    from wander_manager.request_projection import prepare
    history_end = next((index for index, row in enumerate(messages) if row.get('_workshop_zone') in ('context', 'turn')), len(messages))
    provider_messages, projection = prepare(messages + [{'role': 'user', 'content': contract}], model=DEEPSEEK_FLASH_MODEL, base_url=DEEPSEEK_FLASH_API_URL or DEEPSEEK_API_URL, history_end=max(1, history_end), context_end=len(messages))
    result = await call_flash_json_detailed(provider_messages, image_inputs=images, max_tokens=3072, temperature=0.7, enable_thinking=False, route_label='workshop_k_visit', api_key=DEEPSEEK_FLASH_API_KEY or DEEPSEEK_API_KEY, api_url=DEEPSEEK_FLASH_API_URL or DEEPSEEK_API_URL, model_name=DEEPSEEK_FLASH_MODEL)
    if result.status != 'ok' or not isinstance(result.parsed, dict):
        raise RuntimeError('workshop_K_decision_unavailable')
    return result.parsed

async def visit(activity_id, invocation=None):
    global _visiting
    service = get_service()
    key = 'visit:' + activity_id
    with service.store.db() as db:
        saved = db.execute('SELECT result FROM commands WHERE key=?', (key,)).fetchone()
    if saved:
        return json.loads(saved[0])
    if _visiting:
        return {'status': 'unavailable', 'message': 'AI 的工作坊访问正在进行'}
    _visiting = True
    try:
        from session_manager import get_global_session_manager
        session_id = (invocation or {}).get('session_id') or await get_global_session_manager().get_current_session_id()
        from .visit_material import material as visit_material
        material = visit_material(service, invocation, focus_job_id=(invocation or {}).get('focus_job_id'), room=None)
        prepared = (invocation or {}).get('_initial_preview') or {}
        if prepared:
            material['last_action_receipt'] = prepared
            material['delivery_brief'] = prepared.get('delivery_brief')
        refs = []
        failures = []
        focused_job_id = (invocation or {}).get('focus_job_id')
        from .experience import received, action_result
        experience = {'version': 2, 'received': [], 'actions': []}
        visual = prepared.get('visual_attachment')
        visual_ref = prepared.get('artifact') if visual else None
        for index in range(service.store.settings()['visit_rounds']):
            material['remaining_actions'] = service.store.settings()['visit_rounds'] - index
            if (invocation or {}).get('residency'):
                material['collaboration'] = {'mode': 'continuous', 'focus_job_id': focused_job_id, 'mehuman': '本次合作持续到交付处置或明确卡点；本轮只是短回应段，等待时可以聊天'}
            messages = await build_messages(session_id, material)
            from functools import partial
            decider = partial(decide, continuous=True) if (invocation or {}).get('residency') else decide
            if index == 0:
                from .invited_request import decide_first
                choice = await decide_first(decider, messages, visual, invocation)
            else:
                choice = await decider(messages, visual)
            experience['received'].append(received(material))
            if visual_ref:
                service.store.event(visual_ref['job_id'], 'K_received_visual', {'artifact_id': visual_ref['id'], 'revision': visual_ref['revision'], 'source': '本次实际模型图片输入'})
            visual = visual_ref = None
            service.store.acknowledge([row['receipt_id'] for row in material['current_workshop'].get('pending_receipts', [])])
            if choice.get('visit_control') not in ('continue', 'end') or not isinstance(choice.get('action'), dict):
                raise RuntimeError('workshop_K_action_contract_invalid')
            action = choice['action']
            if action.get('action') == 'none':
                break
            if action.get('action') == 'create':
                action.setdefault('visibility', 'private')
            action['request_id'] = key + ':' + str(index)
            from .action_feedback import execute as execute_action
            outcome = await execute_action(service, action)
            feedback = outcome.get('action_feedback')
            if feedback:
                failure = {'action': action.get('action'), 'status': feedback['status'], 'reason': feedback['reason']}
                failure.update({key: feedback[key] for key in ('job_state', 'message') if key in feedback})
                failures.append(failure)
                experience['actions'].append({'action': action.get('action'), 'execution_status': feedback['status'], **{key: feedback[key] for key in ('reason', 'job_state', 'message') if key in feedback}})
                focused_job_id = feedback.get('artifact_job_id') or action.get('job_id') or focused_job_id
                material = visit_material(service, invocation, focus_job_id=focused_job_id, last_action_receipt=outcome)
                if choice['visit_control'] == 'end':
                    break
                continue
            acted_job_id = action.get('job_id') or outcome.get('accepted', {}).get('id')
            visibility = service.store.job(acted_job_id, include_deleted=True)['visibility'] if acted_job_id else 'private'
            experience['actions'].append(action_result(action, outcome, visibility=visibility))
            visual = outcome.pop('visual_attachment', None)
            if visual:
                visual_ref = outcome['artifact']
            if isinstance(outcome.get('browser_material'), dict):
                outcome['browser_material'].pop('screenshot', None)
            refs.append({'action': action['action'], 'job_id': action.get('job_id') or outcome.get('accepted', {}).get('id'), 'revision': action.get('revision')})
            if outcome.get('completion'):
                refs[-1]['disposition'] = outcome['completion']['disposition']
                break
            if outcome.get('confirmation'):
                refs[-1]['confirmation_status'] = outcome['confirmation']['status']
            duplicate_answer = outcome.get('decision_submission', {}).get('status') == 'already_answered'
            if outcome.get('decision_submission'):
                refs[-1]['decision_status'] = outcome['decision_submission']['status']
            timing = choice.get('reply_timing', 'later')
            if timing not in ('wait', 'later'):
                timing = 'later'
            refs[-1]['reply_timing'] = timing
            can_wait = timing == 'wait' and choice['visit_control'] == 'continue' and (not (invocation or {}).get('residency')) and (action['action'] in ('create', 'message')) and (index + 1 < service.store.settings()['visit_rounds'])
            if can_wait:
                from .waiting import reply
                wait = await reply(service, refs[-1]['job_id'])
                refs[-1]['wait_status'] = wait['status']
                outcome = {**outcome, 'reply_wait': wait}
            focused_job_id = acted_job_id or focused_job_id
            material = visit_material(service, invocation, focus_job_id=focused_job_id, last_action_receipt=outcome)
            if action['action'] == 'answer' and (not duplicate_answer) and (not (material.get('current_room') or {}).get('pending_decisions')):
                break
            if choice['visit_control'] == 'end' and (not visual) and (not duplicate_answer) or action['action'] == 'confirm':
                break
            if action['action'] in ('create', 'message') and (not can_wait or refs[-1].get('wait_status') != 'ready'):
                break
        from .visit_observation import capture
        result = {'status': 'success', 'message': 'AI 完成一次工作坊访问；实际动作：' + '、'.join((row['action'] for row in refs)) if refs else 'AI 本次只读取工作坊当前状态后离开；没有发送需求、打开或运行作品，也没有新增验收。', 'actions': refs, 'job_id': focused_job_id or (refs[-1]['job_id'] if refs else ''), 'action_failures': failures, 'evidence': {'action_count': len(refs), 'inspected_job_ids': [row['job_id'] for row in refs if row['action'] == 'inspect'], 'new_plan_confirmation_job_ids': [row['job_id'] for row in refs if row['action'] == 'confirm' and row.get('confirmation_status') not in ('already_confirmed', 'answer_required')], 'new_acceptance_job_ids': [row['job_id'] for row in refs if row['action'] == 'accept'], 'inspection_note': 'inspect 的具体测试结果以原始浏览器回执为准；其他动作不代表打开或运行作品'}, 'observation': capture(service), 'experience': experience}
        if failures:
            if not refs:
                result['message'] = 'AI 本次读取工作坊状态并尝试工具动作，尚未取得成功动作回执。'
            result['message'] += ' 本次未完成的工具动作：' + '、'.join((row['action'] + '（' + row['reason'] + '）' for row in failures)) + '；这些不是成功交付或验收。'
        with service.store.db() as db:
            db.execute('INSERT OR IGNORE INTO commands VALUES(?,?)', (key, json.dumps(result, ensure_ascii=False)))
        return result
    finally:
        _visiting = False
