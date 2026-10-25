"""Freeze actual visit inputs and action receipts, never post-visit guesses.

Ordinary chat and memory keep the workshop's existing public projection.
Private room text remains in its original workshop authority.
"""
from memory_v2.experience_material import text


def _fields(row, keys):
    return {key: text(row[key], 30000) if isinstance(row[key], str) else row[key]
            for key in keys if key in row and isinstance(row[key], (str, int, bool, float))}


def _job(row):
    result = _fields(row, ('id', 'title', 'visibility', 'state', 'revision', 'plan_version',
                          'confirmed_version', 'brief', 'plan'))
    if row.get('actual_delivery'):
        result['actual_delivery'] = _delivery(row['actual_delivery'])
    return result


def _delivery(value):
    def entry(row):
        result = _fields(row, ('id', 'name', 'revision', 'sha256', 'kind', 'accepted_by_k'))
        for key in ('actual_observation', 'browser_observation'):
            if row.get(key):
                result[key] = _fields(row[key], ('kind', 'at', 'status', 'checks_count'))
        return result
    return {**_fields(value, ('source',)),
            'entries': [entry(row) for row in value.get('entries', [])[:8]],
            'supporting_files': [_fields(row, ('id', 'name', 'revision'))
                                 for row in value.get('supporting_files', [])[:8]]}


def _decision(row):
    return {**_fields(row, ('id', 'revision', 'plan_version', 'created_at', 'status')),
            'questions': [{**_fields(q, ('title',)),
                           'options': [text(v, 1000) for v in q.get('options') or [] if isinstance(v, str)]}
                          for q in row.get('questions') or []]}


def received(material):
    """Called only after a successful AI invocation with this exact material."""
    result = {'jobs': [_job(row) for row in (material.get('current_workshop') or {}).get('jobs', [])
                       if row.get('visibility') == 'public']}
    public_job_ids = {row['id'] for row in result['jobs'] if row.get('id')}
    cabinet = (material.get('current_workshop') or {}).get('cabinet') or {}
    if material.get('entry_context'):
        result['entry_context']=_fields(material['entry_context'],('event_type','source'))
    entries=(material.get('current_workshop') or {}).get('wander_entries')
    if entries:
        result['entry_status']=_fields(entries,('status','summary','ongoing_jobs_count','needs_k_count'))
    if 'cabinet_entries' in material:
        result['cabinet_entries']=[_fields(row,('id','job_id','job_title','revision','name','accepted_by_k','job_archived'))
            for row in material['cabinet_entries'] if row.get('visibility')=='public']
    if 'lifecycle' in (material.get('current_workshop') or {}):
        result['lifecycle'] = _fields(material['current_workshop']['lifecycle'], ('accepted', 'archive_source'))
        result['current_jobs_count'] = material['current_workshop'].get('total_jobs')
    if cabinet:
        result['cabinet'] = _fields(cabinet, ('visit_count','last_visited_at','unread_visits','archived_jobs_count','source'))
    collections = (material.get('current_workshop') or {}).get('private_collections')
    if isinstance(collections, dict):
        result['private_collections'] = _fields(collections, ('total',))
    result['historical_receipts'] = [{**_fields(row, ('receipt_id', 'job_id', 'kind', 'created_at')),
                                     'body': _fields(row.get('fact_excerpt') or {},
                                         ('summary', 'text', 'plan', 'revision', 'plan_version', 'state'))}
                                    for row in (material.get('current_workshop') or {}).get('pending_receipts', [])
                                    if row.get('job_id') in public_job_ids or row.get('kind') == 'cabinet_visited']
    room = material.get('current_room') or {}
    job = room.get('job') or {}
    if job.get('visibility') == 'public':
        result['room'] = {'job': _job(job), 'messages': [
            _fields(row, ('seq', 'role', 'body', 'created_at')) for row in room.get('messages', [])],
            'pending_decisions': [_decision(row)
                                  for row in room.get('pending_decisions', [])]}
        if room.get('actual_delivery'):
            result['room']['job']['actual_delivery'] = _delivery(room['actual_delivery'])
        # This receipt was actually supplied to AI, unlike the final frontier
        # captured after leaving. Artifact text is not a completed review.
        receipt = material.get('last_action_receipt') or {}
        artifact = receipt.get('artifact') or {}
        if artifact.get('job_id') == job.get('id'):
            result['inspection'] = {'artifact': _fields(artifact, ('id', 'job_id', 'revision', 'title', 'name', 'sha256')),
                                    **_fields(receipt, ('text', 'truncated', 'material'))}
            browser = receipt.get('browser_material') or {}
            if browser:
                result['inspection']['browser'] = _fields(browser, ('text', 'status', 'source'))
    elif job:
        result['private_room_present'] = True
    return result


def action_result(action, outcome, *, visibility):
    """AI's submitted act and the returned execution status stay distinct."""
    result = {'action': text(action.get('action'), 80)}
    if action.get('action') == 'works':
        cabinet = outcome.get('cabinet') or {}
        return {**result, 'cabinet': {
            'visits':_fields(cabinet.get('visits') or {}, ('visit_count','last_visited_at','unread_visits')),
            'artifacts':[_fields(row, ('id','job_id','job_title','revision','accepted_by_k','view_count','last_viewed_at','job_archived'))
                         for row in cabinet.get('artifacts') or [] if row.get('visibility') == 'public'],
            'private_works_present':any(row.get('visibility') == 'private' for row in cabinet.get('artifacts') or [])}}
    if visibility != 'public':
        return {**result, 'private': True}
    result.update(_fields(action, ('job_id', 'revision', 'plan_version', 'artifact_id', 'text','disposition')))
    if isinstance(action.get('answers'), list):
        result['answers'] = [text(value) for value in action['answers'] if isinstance(value, str)]
    confirmation = outcome.get('confirmation') or {}
    decision = outcome.get('decision_submission') or {}
    if confirmation.get('status'):
        result['confirmation_status'] = text(confirmation['status'], 80)
    if decision.get('status'):
        result['decision_status'] = text(decision['status'], 80)
    result_job = next((row for row in outcome.get('jobs') or [] if row.get('id') == action.get('job_id')), None)
    if result_job:
        result['result_job'] = _fields(result_job, ('revision', 'state', 'plan_version', 'confirmed_version'))
    browser = outcome.get('browser_material') or {}
    if browser:
        result['browser_status'] = text(browser.get('status'), 80)
        result['checks'] = [_fields(row, ('action', 'selector', 'value', 'status', 'error'))
                            for row in browser.get('checks') or []]
    return result


def project(value):
    if not isinstance(value, dict) or value.get('version') not in (1, 2):
        return ''
    lines, seen = [], set()

    def add(line):
        if line and line not in seen:
            seen.add(line)
            lines.append(line)

    for material in value.get('received') or []:
        if material.get('private_collections'):
            add(f"AI来访时收到的已完成、尚未分享保密收藏数量：{material['private_collections'].get('total',0)}；这是收藏统计，不是待办。")
        if material.get('entry_context'):
            add('AI本次计划选择的工作坊入口：'+material['entry_context']['event_type']+'；计划目的与实际动作另列。')
        if material.get('entry_status'):
            add('AI来访时收到的当前工作坊概况：'+material['entry_status']['summary'])
        for entry in material.get('cabinet_entries') or []:
            add(f"AI本次收到的作品目录：《{entry.get('job_title')}》需求v{entry.get('revision')}，文件{entry.get('name')}，作品ID{entry.get('id')}；收到目录不等于打开或验收。")
        if material.get('lifecycle'):
            add('AI来访时收到的工单状态定义：accepted表示' + material['lifecycle']['accepted'] + '。')
            add(f"本次收到的当前未归档工单数量：{material.get('current_jobs_count')}。")
        cabinet = material.get('cabinet') or {}
        if cabinet:
            add(f"AI来访时收到作品柜的参观记录：人类伙伴累计参观 {cabinet.get('visit_count',0)} 次，最近时间 {cabinet.get('last_visited_at') or '尚无记录'}。")
            if value['version'] >= 2 and 'archived_jobs_count' in cabinet:
                add(f"AI来访时收到作品柜历史工单数量：{cabinet['archived_jobs_count']}；历史房间与作品仍可查询。")
        room = material.get('room') or {}
        job = room.get('job') or {}
        jobs = [job] if job else material.get('jobs') or []
        for item in jobs:
            if item.get('visibility') != 'public':
                continue
            add(f"AI来访时看到工单《{item.get('title') or '未命名'}》：需求版本{item.get('revision')}，"
                f"方案版本{item.get('plan_version')}，已确认版本{item.get('confirmed_version')}，当时状态{item.get('state')}。")
            if item.get('brief'):
                add('当时的需求：' + item['brief'])
            if item.get('plan'):
                add('当时收到的方案正文：' + item['plan'])
            delivery = item.get('actual_delivery') or {}
            for entry in delivery.get('entries') or []:
                add(f"当时收到的真实交付文件：{entry.get('name')}，作品ID {entry.get('id')}，需求版本{entry.get('revision')}，类型{entry.get('kind')}；此文件的AI验收记录={entry.get('accepted_by_k')}。")
                observation = entry.get('browser_observation') or entry.get('actual_observation') or {}
                if observation:
                    add(f"该文件已有查看回执：{observation.get('kind')}，发生时间{observation.get('at')}，状态{observation.get('status')}，实际断言数{observation.get('checks_count',0)}；这是已有回执，不代表本次重新测试。")
            for entry in delivery.get('supporting_files') or []:
                add(f"当时收到的制作支持文件：{entry.get('name')}；它不是作品展示入口。")
        for row in room.get('messages') or []:
            label = {'k': 'AI原话', 'human': '人类伙伴原话', 'desktop_codex': '桌面工具手原话',
                     'codex': '工具手原话'}.get(row.get('role'), '工作坊合作方原话')
            add(f"{label}：{row.get('body') or ''}")
        for decision in room.get('pending_decisions') or []:
            for question in decision.get('questions') or []:
                add('工具手当时提出的问题：' + (question.get('title') or '')
                    + ('；可选项：' + '、'.join(question['options']) if question.get('options') else ''))
        for receipt in material.get('historical_receipts') or []:
            body = receipt.get('body') or {}
            detail = '；'.join(str(body[key]) for key in ('summary', 'text', 'plan') if body.get(key))
            if detail:
                add(f"AI当时收到的旧回执（{receipt.get('created_at') or '时间未标注'}，"
                    f"当时需求版本{body.get('revision')}、方案版本{body.get('plan_version')}）：{detail}")
        inspection = material.get('inspection') or {}
        if inspection.get('material'):
            add('AI当时实际收到的查看材料：' + inspection['material']
                + '；文件 ' + str((inspection.get('artifact') or {}).get('name') or (inspection.get('artifact') or {}).get('id') or '')
                + '。图片输入不等于 HTML 页面交互验证。')
        if inspection.get('text'):
            add('AI当时收到的作品正文摘录：' + inspection['text'])
        browser = inspection.get('browser') or {}
        if browser.get('text'):
            add('AI当时收到的作品页面文字：' + browser['text'])
    for row in value.get('actions') or []:
        if row.get('job_state'):
            add('本次工具拒绝回执的当前工单状态：' + row['job_state'] + '；' + (row.get('message') or ''))
        if row.get('private'):
            continue
        if row.get('execution_status') in ('rejected','unavailable'):
            add(f"AI尝试的工作坊动作未完成：{row.get('action')}；工具回执 {row.get('reason')}。这不是成功检查、交付或验收。")
            continue
        add('AI本次提交的工作坊动作：' + str(row.get('action') or '')
            + (f"；需求版本{row['revision']}" if 'revision' in row else '')
            + (f"；方案版本{row['plan_version']}" if 'plan_version' in row else ''))
        for artifact in (row.get('cabinet') or {}).get('artifacts') or []:
            add(f"AI的作品柜查询回执记录《{artifact.get('job_title')}》v{artifact.get('revision')}：人类伙伴看过 {artifact.get('view_count',0)} 次，最近时间 {artifact.get('last_viewed_at') or '尚无记录'}。")
        if row.get('text'):
            add('AI提交的原话：' + row['text'])
        if row.get('answers'):
            add('AI提交的答复：' + '；'.join(row['answers']))
        if row.get('disposition'):
            add('AI本次真实成品处置：' + {'share':'验收并分享','keep':'验收收藏，保持当前可见性','keep_private':'验收并继续保密'}.get(row['disposition'],row['disposition']))
        for key in ('confirmation_status', 'decision_status', 'browser_status'):
            if row.get(key):
                add('本次动作的执行回执：' + row[key])
        if row.get('result_job'):
            job = row['result_job']
            add(f"动作返回的工单状态：{job.get('state')}；需求版本{job.get('revision')}，"
                f"方案版本{job.get('plan_version')}，已确认版本{job.get('confirmed_version')}。")
        for check in row.get('checks') or []:
            add('实际页面检查：' + '；'.join(str(check.get(key) or '') for key in ('action', 'value', 'status')))
    return '\n'.join(lines)


def _summary_refs(value):
    """Stable public references for a later, on-demand workshop read."""
    refs = {'jobs': [], 'artifacts': []}
    seen_jobs, seen_artifacts = set(), set()

    def job_ref(row):
        job_id = str(row.get('id') or '').strip()
        if not job_id or job_id in seen_jobs:
            return
        seen_jobs.add(job_id)
        refs['jobs'].append({key: row.get(key) for key in
                             ('id', 'title', 'revision', 'state')
                             if row.get(key) is not None})

    def artifact_ref(row):
        artifact_id = str(row.get('id') or '').strip()
        if not artifact_id or artifact_id in seen_artifacts:
            return
        seen_artifacts.add(artifact_id)
        refs['artifacts'].append({key: row.get(key) for key in
                                  ('id', 'name', 'job_id', 'job_title', 'revision')
                                  if row.get(key) is not None})

    for material in value.get('received') or []:
        entry = material.get('entry_context') or {}
        if entry.get('event_type'):
            refs['entry'] = entry['event_type']
        for row in material.get('cabinet_entries') or []:
            artifact_ref(row)
        room = material.get('room') or {}
        jobs = [room.get('job')] if room.get('job') else material.get('jobs') or []
        for job in jobs:
            if job.get('visibility') not in (None, 'public'):
                continue
            job_ref(job)
            for row in (job.get('actual_delivery') or {}).get('entries') or []:
                artifact_ref(row)
        artifact = (material.get('inspection') or {}).get('artifact') or {}
        if artifact:
            job_ref(artifact)
            artifact_ref(artifact)
    for row in value.get('actions') or []:
        if row.get('private'):
            continue
        if row.get('result_job'):
            job_ref(row['result_job'])
        for row in (row.get('cabinet') or {}).get('artifacts') or []:
            artifact_ref(row)
    refs['jobs'] = refs['jobs'][:8]
    refs['artifacts'] = refs['artifacts'][:8]
    return refs


def summarize(value, *, limit=1200):
    """Bounded memory/day-end view: what happened and where the details live.

    Whole lines only.  Full plan text, room transcripts and artifact/page
    bodies stay in the workshop authority; they are referenced, not copied.
    A single line that alone exceeds the budget is kept whole rather than cut.
    """
    if not isinstance(value, dict) or value.get('version') not in (1, 2):
        return '', {}
    rows = []  # (priority, line)
    seen = set()

    def add(priority, line):
        line = str(line or '').strip()
        if line and line not in seen:
            seen.add(line)
            rows.append((priority, line))

    for material in value.get('received') or []:
        entry = material.get('entry_context') or {}
        if entry.get('event_type'):
            add(10, '本次工作坊入口：' + str(entry['event_type']) + '。')
        status = material.get('entry_status') or {}
        if status.get('summary'):
            add(40, '来访时概况：' + str(status['summary']))
        for row in (material.get('cabinet_entries') or [])[:8]:
            add(45, f"作品目录：《{row.get('job_title') or '未命名'}》"
                    f"v{row.get('revision')}，{row.get('name') or '文件未标注'}，"
                    f"作品ID {row.get('id')}。")
        room = material.get('room') or {}
        jobs = [room.get('job')] if room.get('job') else material.get('jobs') or []
        for job in jobs:
            if job.get('visibility') not in (None, 'public'):
                continue
            add(15, f"工单《{job.get('title') or '未命名'}》：状态{job.get('state')}，"
                    f"需求版本{job.get('revision')}，方案版本{job.get('plan_version')}，"
                    f"已确认版本{job.get('confirmed_version')}。")
            for row in (job.get('actual_delivery') or {}).get('entries') or []:
                add(25, f"交付文件：{row.get('name') or '文件未标注'}"
                        f"（作品ID {row.get('id')}，类型{row.get('kind')}）。")
                observation = (row.get('browser_observation')
                               or row.get('actual_observation') or {})
                if observation:
                    add(25, "该文件已有查看回执："
                            f"{observation.get('kind')}，状态{observation.get('status')}，"
                            f"实际断言数{observation.get('checks_count', 0)}。")
        for decision in room.get('pending_decisions') or []:
            for question in (decision.get('questions') or [])[:2]:
                if question.get('title'):
                    add(35, '工具手当时提问：' + str(question['title']))
        artifact = (material.get('inspection') or {}).get('artifact') or {}
        if artifact:
            add(20, f"实际查看：{artifact.get('name') or artifact.get('title') or artifact.get('id')}"
                    f"（作品ID {artifact.get('id')}）。")
        for receipt in (material.get('historical_receipts') or [])[:4]:
            body = receipt.get('body') or {}
            detail = str(body.get('summary') or '').strip()
            if detail:
                add(50, f"旧回执（{receipt.get('created_at') or '时间未标注'}）：{detail}")

    for row in value.get('actions') or []:
        if row.get('private'):
            continue
        action = str(row.get('action') or '').strip()
        if row.get('execution_status') in ('rejected', 'unavailable'):
            add(15, f"动作未完成：{action}；回执 {row.get('reason')}。")
        elif action:
            add(15, 'AI 的动作：' + action
                    + (f"；需求版本{row['revision']}" if 'revision' in row else '')
                    + (f"；方案版本{row['plan_version']}" if 'plan_version' in row else '') + '。')
        if row.get('text'):
            add(10, 'AI 提交的原话：' + str(row['text']))
        for key, label in (('confirmation_status', '确认回执'),
                           ('decision_status', '决定回执'),
                           ('job_state', '工具拒绝时的工单状态')):
            if row.get(key):
                add(30, f"{label}：{row[key]}。")
        if (row.get('result_job') or {}).get('state'):
            add(25, '动作返回工单状态：' + str(row['result_job']['state']) + '。')
        if row.get('answers'):
            add(25, 'AI 提交的答复：' + '；'.join(str(item) for item in row['answers']))

    lines, used, skipped = [], 0, 0
    for _, line in sorted(rows, key=lambda item: item[0]):
        # Never cut a line.  A line that alone exceeds the budget is still kept
        # whole; only lines that could fit are dropped when the budget is full.
        if used and len(line) <= limit and used + len(line) + 1 > limit:
            skipped += 1
            continue
        lines.append(line)
        used += len(line) + 1
    if skipped:
        lines.append('（其余 %d 条材料未展开；完整内容在作品柜/工坊记录中可查。）' % skipped)
    return '\n'.join(lines), _summary_refs(value)
