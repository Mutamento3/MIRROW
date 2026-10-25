"""Request-local choices decoded into the existing strict habit contract."""
from . import books
from .topics import sections

VERSION = 'habit-local-selection-v1'
SPAN_VERSION = 'habit-source-span-v2'
CITATION_SELECTION = 'habit-citation-selection-v1'
MESSAGE_EVIDENCE = 'habit-full-message-evidence-v1'
MESSAGE_GUIDE = '''evidence_selection_contract=habit-full-message-evidence-v1 的请求选择整条消息作为证据。
每条认识的 evidence 只需返回 [{"n":消息编号}]，程序将从冻结来源取回所选消息的完整原话，并保留说话人、时间和身份。
必要的相邻消息一起选择；每条认识仍须被所选消息支持。整条消息被选中只代表它接受审核，不代表其中所有话都是真实事实或支持该认识。
如显式返回 quote，则仍按逐字引文校验，程序不会修正错误引文。
'''
CITATION_GUIDE = '''citation_selection_contract=habit-citation-selection-v1 的请求将所选认识及其 evidence 一起交给独立审核。
阅读完整情境后，为每条完整认识选齐能证明其中事实与限定的原话。情境、对象或指代依赖相邻消息时，把必要的相邻原话也选入该条 evidence，并保留说话人；本人观点由本人表达证明，AI 的发言可以说明交流语境。
逐条对照 claim 与已选 evidence：确有依据的因果和等义表达可以支持；旧文里的额外意图、范围、频率或跨情境比较需要各自依据。只支持其中一部分时，选取较短而语义完整、仍保留条件的旧文片段；其余部分保持未复核。
用原话完整表达支持关系即可，无需添加审核结论、解释或新认识。'''
SPAN_GUIDE = '''output_contract=habit-source-span-v2 的请求采用原文片段合同。
q 选择请求，t 选择该请求 targets 中的目标；每个目标的 text 就是完整旧栏目，在本请求内直接阅读。
sources 直接列出本请求允许的 messages.n，证据和 sources 均使用同一个 n，无需换算。
每个请求返回一项 habit_reviews:{"q":1,"outcome":"updated|supported_no_change|insufficient|conflicting",
"claims":[{"t":1,"claim":"旧栏目中语义完整的连续逐字片段","evidence":[{"n":1,"quote":"该消息中的连续逐字原话"}]}],"sources":[]}。
supported_no_change 选择材料实际支持的具体认识；一段旧文可以包含多个事实，逐条选择有支持的部分，必要时为同一认识提供多条证据。
claim 在完整栏目中保留原有主体、否定、条件、频率和时间含义；若片段依赖前文才能成立，选取包含该前文的完整片段。没有完整可支持的认识就用 insufficient。
claims 的证据由程序汇总，supported_no_change 的 sources 留空；其他 outcome 的 claims 留空，sources 列相关 n，insufficient 可以为空。
一次称呼或一次讨论只证明该次事实；频率与习惯需要相应原话或跨次证据。同次经历的复述、计划与已发生分别判断，未提及的日子保持未知。
baseline_bound 只复核指定目标；批准仅覆盖列出的具体片段，其余旧认识仍未复核。旧书是待核命题而非证据，材料不足保持原书。
updated 仍需 proposals、独立审核及真实入书结果；7/28/90 是复核周期。
'''
GUIDE = '''habit_review_requests 是待复核材料。每个请求的 q、目标 t、原认识 c、来源 s 都是本请求内的选择编号。
messages 保留完整原话和说话人、时间；sources 中的 n 指向 messages，回答时使用该请求的 s。
targets 的 g 指向共享 habit_target_choices 原句目录。目录 claims 按原顺序保留完整栏目，只是原标点排版，不是已证明的独立命题；结合完整栏目选择具体认识，保留承接条件。
每个请求输出一项 habit_reviews:{"q":1,"outcome":"updated|supported_no_change|insufficient|conflicting",
"claims":[{"t":1,"c":[1],"evidence":[{"s":1,"quote":"连续逐字原话"}]}],"sources":[]}。
supported_no_change 用 claims 选择原认识及证据；c 可选连续数段以保留完整条件，sources 留空，程序从 evidence 汇总引用。
其他 outcome 的 claims 留空，用 sources 列本请求来源 s；insufficient 可为空。编号和所选原文由程序还原，无需另写长ID、版本、targets、claim或reference_ids。
updated 表示提出修订，实际入书仍依赖 proposals 及独立审核；材料不足或冲突保持相应结果。
复核具体行为、情境、频率与观察跨度；一次经历的反复讨论不是多次发生，计划不是已发生，未提及的日子保持未知。
7/28/90 是检查周期，旧书不是证据；baseline_bound 请求只复核指定认识。批准只覆盖所选具体认识。
'''


def enabled(request):
    return request.get('output_contract') in {VERSION, SPAN_VERSION}


def guide(requests):
    versions = {r.get('output_contract') for r in requests}
    all_messages = bool(requests) and all(r.get('output_contract') == SPAN_VERSION and
        r.get('evidence_selection_contract') == MESSAGE_EVIDENCE for r in requests)
    span_guide = (SPAN_GUIDE.replace('{"n":1,"quote":"该消息中的连续逐字原话"}', '{"n":1}')
                  if all_messages else SPAN_GUIDE)
    text = '\n'.join(text for version, text in ((VERSION, GUIDE), (SPAN_VERSION, span_guide))
                     if version in versions)
    if any(r.get('citation_selection_contract') == CITATION_SELECTION for r in requests):
        text += '\n' + (CITATION_GUIDE.replace('用原话完整表达支持关系即可', '选择能够共同支持认识的完整消息编号即可')
                        if all_messages else CITATION_GUIDE)
    if any(r.get('output_contract') == SPAN_VERSION and
           r.get('evidence_selection_contract') == MESSAGE_EVIDENCE for r in requests):
        text += '\n' + MESSAGE_GUIDE
    return text


def choices(request, snapshot):
    allowed = request.get('baseline_targets', [])
    result = []
    for domain in books.DOMAINS:
        for entry in snapshot.get(domain, []):
            if entry.get('subject_id') != request['subject_id']:
                continue
            for title, body in sections(entry['body']):
                target = {'domain': domain, 'entry_id': entry['entry_id'],
                          'revision': entry['revision'], 'section': title}
                if allowed and target not in allowed:
                    continue
                # Reuse only its lossless layout helper, not the opt-in reviewer.
                from .habit_clause_review import review_units
                units = review_units(body)
                result.append({'target': target, 'section_text': body, 'units': units,
                               'name': entry.get('name', '')})
    return result


def material(requests, n_of, snapshot, catalogue=None):
    from .habit_frequency_evidence import model_rows
    rows = []
    for q, request in enumerate(requests, 1):
        if not enabled(request):
            from .habit_review import model_requests
            rows.extend(model_requests([request], n_of))
            continue
        targets = choices(request, snapshot)
        span = request.get('output_contract') == SPAN_VERSION
        target_rows = []
        for t, item in enumerate(targets, 1):
            body = {'name': item['name'], 'section': item['target']['section'],
                    **({'text': item['section_text']} if span else
                       {'claims': [{'c': c, 'text': text} for c, text in enumerate(item['units'], 1)]})}
            if catalogue is None or span:
                target_rows.append({'t': t, **body})
            else:
                # Identity is retained privately in the catalogue too. Equal
                # prose in different books must not collapse their provenance.
                entry = {'target': item['target'], **body}
                if entry not in catalogue:
                    catalogue.append(entry)
                target_rows.append({'t': t, 'g': catalogue.index(entry) + 1})
        rows.append({**{k: v for k, v in request.items() if k not in {
            'id', 'reference_ids', 'baseline_targets', 'frequency_proof', 'frequency_evidence'}},
            'q': q, 'baseline_bound': bool(request.get('baseline_targets')),
            'frequency_evidence': model_rows(request.get('frequency_evidence', []), n_of),
            'sources': ([n_of[mid] for mid in request['reference_ids']] if span else
                        [{'s': s, 'n': n_of[mid]} for s, mid in enumerate(request['reference_ids'], 1)]),
            'targets': target_rows})
    return rows


def _at(items, number):
    if type(number) is not int or not 1 <= number <= len(items):
        raise ValueError('local_choice_out_of_range')
    return items[number - 1]


def decode(row, request, space, snapshot):
    if set(row) - {'q', 'outcome', 'claims', 'sources'}:
        raise ValueError('local_report_fields')
    n_of = {e['id']: n for n, e in enumerate(space, 1)}
    sources = request['reference_ids']
    span = request.get('output_contract') == SPAN_VERSION
    def source_number(number):
        if not span:
            return n_of[_at(sources, number)]
        mid = _at(space, number)['id']
        if mid not in sources:
            raise ValueError('local_source_out_of_scope')
        return number
    targets = choices(request, snapshot)
    claims, bound, used = [], [], set()
    selections = row.get('claims', [])
    if not isinstance(selections, list) or not isinstance(row.get('sources', []), list):
        raise ValueError('local_report_list')
    if row.get('outcome') == 'supported_no_change':
        if not selections or row.get('sources'):
            raise ValueError('local_claim_selection_required')
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) != {'t', 'claim' if span else 'c', 'evidence'}:
                raise ValueError('local_claim_fields')
            item = _at(targets, selection['t'])
            if span:
                claim = selection['claim']
                if not isinstance(claim, str) or not claim.strip() or claim not in item['section_text']:
                    raise ValueError('local_claim_not_in_target')
            else:
                indexes = selection['c']
                if (not isinstance(indexes, list) or not indexes or any(type(i) is not int for i in indexes)
                        or indexes != list(range(indexes[0], indexes[-1] + 1))):
                    raise ValueError('local_claim_contiguous')
                claim = ''.join(_at(item['units'], c) for c in indexes)
            if item['target'] not in bound:
                bound.append(item['target'])
            if not isinstance(selection['evidence'], list):
                raise ValueError('local_evidence_list')
            refs = []
            for evidence in selection['evidence']:
                full_message = (span and request.get('evidence_selection_contract') == MESSAGE_EVIDENCE
                                and isinstance(evidence, dict) and set(evidence) == {'n'})
                if not full_message and (not isinstance(evidence, dict) or set(evidence) != {'n' if span else 's', 'quote'}):
                    raise ValueError('local_evidence_fields')
                n = source_number(evidence['n' if span else 's'])
                refs.append({'n': n, 'quote': _at(space, n)['text'] if full_message else evidence['quote']})
                used.add(n)
            claims.append({'target_index': bound.index(item['target']) + 1, 'claim': claim, 'evidence': refs})
    else:
        if selections:
            raise ValueError('local_nonclaim_selection')
        numbers = row.get('sources', [])
        for s in numbers:
            used.add(source_number(s))
        if len(used) != len(numbers):
            raise ValueError('local_duplicate_source')
    return {'id': request['id'], 'outcome': row.get('outcome'), 'targets': bound,
            'reference_ids': sorted(used), **({'claim_support': claims} if claims else {})}


def decode_rows(values, requests, space, snapshot):
    result = []
    for row in values:
        if not isinstance(row, dict) or 'q' not in row:
            result.append(row)
            continue
        request = None
        try:
            request = _at(requests, row['q'])
            if not enabled(request):
                raise ValueError('local_contract_not_enabled')
            result.append(decode(row, request, space, snapshot))
        except (ValueError, KeyError, TypeError) as exc:
            result.append({'id': request['id'] if request else '', '_local_error':
                           str(exc) if isinstance(exc, ValueError) else 'local_material_invalid'})
    return result
