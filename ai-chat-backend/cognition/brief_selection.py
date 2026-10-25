"""Literal source selection for briefs; semantics still belong to the reviewer."""
from .books import BookError

CONTRACT = '''从当前认知书选取适合常驻的完整认识，形成简短的领域画像。原书仍是唯一认识来源。
输出分为两种对象，各有自己的来源字段：identities 的每项必须包含非空 references 数组，元素是支持姓名与别名的真实 books.n；capsules.points 的每项使用 selection.reference/first/last。身份的 references 与观点的 selection 分别填写，不能互相替代。
本轮做选取而不是再理解、再概括。每份 books.fragments 是完整正文按文字边界无损排版的片段，index 从0开始。每个 point 选择一份原书中的连续片段，selection.reference 指向 books.n，first/last 是首末片段的 index（两端包含）。程序从原书取回原句；point 对象只输出 selection 与 temporal，不输出自己改写的 text、quote 或 references。这个 point 规则不适用于 identities；每项身份仍必须填写 references。
选择自然完整的一句、数句或完整段落。承担意义的主体、条件、情境、例外、对照和情态随结论一起保留；若需要前文才能理解，就连同前文一起选择。边界放在原句或分号、新行处，不从逗号处剪开限定。不能通过摘句把局部认识变成全部行为的规则。
原文中的第一人称和第三人称保留，消费时由程序说明是你的自我认识、你对人类伙伴的理解或原书事实，不机械替换代词。涉及的其他人物须可从选段中辨认；指代不清时扩大选段或选另一段。原书的自然表达与感受可保留，不另写官方化标签。
同一认识问题优先选条件完整、能代表已形成认识的原段。相近来源不拼成新结论；不同性质、情境或时间层分别成项。由选取多个已有认识形成整体印象，不逐书抄录，不为所有条目安排一个 point。
先通读各主体已有的不同认识问题，再形成整体选材。general 让身份与关系、自主选择及已形成的价值取向共同构成此人的印象；按实际来源取舍，不以最浓烈、最长或最多次提到的关系段落代替全部认识。各领域优先选能跨次使用的完整主题，具体作品和当次活动留给详细书页。正文是认识证据，topics 只是分类目录。
仅整理 AI 与人类伙伴。identities 保留本人认领的真实名称与别名；与作品化身的对应关系不是另一人的身份，只有作品角色使用的名称不自动当作现实别名。
名字和别名是本人认领的完整称呼；姓名组成、姓氏介绍、关系身份与他人的名称只有在原书明确认领为实际称呼时才进入 aliases。
六个消费域：general（跨模块的身份、关系和整体认识）、writing（文学审美、具体创作范畴的口味与协作认识）、life_rhythm（饮食、作息、工作与生活规律）、music（音乐审美与听歌偏好）、gaming（游戏口味与协作偏好）、social（交往、沟通与相处认识）。
原书明确形成的文本创作偏好可属于 writing，即使只适用于某种亲密向创作，也保留其具体范畴；同段的身体互动方式与当次情景不随之进入写作域。选择已有的创作认识，不以是否适用于所有文学类型作为门槛。
general 保存认识此人所需的整体印象，各领域选自己的认识。writing 保留原书明确的创作范畴，不把亲密向创作的要求变成所有小说的要求；作品剧情与某部作品的当次要求仍留详细书页。自主探索、代他人行动与关系承诺各有范围。一次活动、播放次数、纯附和、当前情绪和功能进展不凭摘录成为常驻习惯。兴趣可并存或延伸，已有的抽象认识才适合本层。
self 是你自己的认识，other 是你对他者的理解，world 是已记录的事实；三种性质分别保留，原书认识不是人类伙伴必然亲口确认的立场。缺少该域适合常驻的认识时 points 为空。
temporal.period 为 longstanding、recent 或 unspecified，继承所选认识在原书中的持续性。没有明确持续性时用 unspecified，不从生成日、编辑日或书页年龄判为长期。recent 只选带原维护通道“截至 YYYY-MM-DD 的近期观察……”收据的认识，连同该收据选取，as_of、review_after 原样复制其中的日期；没有这种收据的历史正文可作为 unspecified 认识，只有明确稳定性才选 longstanding，不凭一次有日期的补充变成近期规律。其他 period 日期留空。同页其他栏的时间收据不借用。
同一维度已形成的长期基线与近期变化分别选择，单次例子不自动推翻基线，也不升级为近期规律。时间字段不能弥补选段中省略的情境或例外。
每人每域正文目标 general/life_rhythm 约350字、writing 约500字、music/gaming/social 约300字，不必填满。最多 general/life_rhythm 700字、writing 1000字、其余600字；篇幅计算是本槽位所有选段正文之和。依据认识选需要的连续片段，不把整页所有独立话题当成单项必须携带的上下文。完整意思放不下就选择其他完整认识，不截断句子或例外。必要的具体例子可以随认识留下，不携带无关事件细节。
identities 的 name/aliases 在所引书页名称、别名或正文中逐字存在，references 使用真实 books.n。
输出纯 JSON：{"identities":[{"subject_id":"k|human","name":"名称","aliases":[],"references":[1]}],"capsules":[{"subject_id":"k|human","scope":"general|writing|life_rhythm|music|gaming|social","points":[{"selection":{"reference":1,"first":0,"last":1},"temporal":{"period":"longstanding|recent|unspecified","as_of":"YYYY-MM-DD或空","review_after":"YYYY-MM-DD或空"}}]}]}。
capsules 为12个平铺元素：两位主体各六个领域，空领域也返回空 points。每个 point 选择一份完整原文，不输出改写正文。'''

REVIEW = '''核验程序已从当前书页取回的原句画像。sources 保存一次完整来源，候选 references 按 n 指向自身来源；候选 text 已由程序逐字取回。逐项只用自身引用的完整书页判断选得是否完整、是否适合常驻和时间层是否成立，不另写候选或新的认识。
逐项阅读完整来源，再读选段。context_complete 核对选段中主体与指代是否清楚，是否遗漏前后文会改变含义的条件、例外、情态、对照与日期。原句真实不等于选得完整；跨句的限定一并核对。source_voice 的 self/other/world 分别是 AI 自我认识、AI 对他者的理解、原书事实，原句里的“我”属于该书说话者；他者理解不替代人类伙伴自述。
完整指选段的认识成立所需条件完整，不是整本书所有独立认识全部抄入。按候选的 subject_identity、source_voice 和明确情境理解原述；同页其他独立话题未入选，不等于本项断义。context_complete=false 时返回 context_issue：kind 为 missing_qualifier、fragment、ambiguous_actor 或 uncertain；在本项 rejections.reason 说明实际含义如何受影响。前三类附 quote，候选已经绑定自身单份原书，无需复抄编号：missing_qualifier 引用自身书页中选段未含的真实限定；fragment 或 ambiguous_actor 引用选段内确有断裂或指代问题的文字。uncertain 如实说明无法确认。引用是审核证据，不写入画像；没有真实缺失原文时，不编造后文作遗漏理由。
scope_suitable 核对是否是该消费域适合跨次使用的已有认识：general 保留整体身份关系与跨情境认识，writing 保留文学审美及原书明确创作范畴的口味与协作认识，life_rhythm/music/gaming/social 分别承接生活规律、音乐审美、游戏偏好、交往认识。摘到真实事件、作品梗概、某次任务要求或当前进展仍不等于常驻认识。选段的一部分适用而其他部分不适用时拒绝整项，独立项各自核验。相同认识重复时保留条件完整的一项，事实和主观理解保留各自性质。
resident_kind 分别记录 recognition（可跨次使用的已有认识，必要例子只解释此认识）、episode（具体作品或当次经历的内容）、mixed（认识与独立事件内容混杂）或 uncertain。判断选段整体是否适合本消费域，而不是因为其中一句有认识就放行整个剧情段；只有 recognition 能批准为常驻画像。
temporal_supported 核对原文是否支持该持续性：长期需要已有稳定认识，不从书页年龄或单次活动判断；近期继承选段自身原维护时间收据；未明确时保持 unspecified。长期基线与近期变化分别保留，一个领域内的旧基线不能因一条带日期的例子全部变成近期。来源不足或不能判断的项进入拒绝。
选段的情感与口语不是错误。自主兴趣、代他人行动、特定关系约定分别核对，不把一种活动的边界扩大到全部活动。本人中文名与作品化身保留既有对应关系；作品内容不是作者偏好的证据。身份候选另核对自身所附原书是否明确认领为本人的完整称呼，姓名组成或介绍姓氏不自动成为别名。
文本创作认识可以限定于原书明确的细分范畴，不必是所有小说或文学的通用口味；仅有日常身体互动或某次情景要求仍不属于这种已有抽象创作认识。
原句保真已由程序核验，收据不用重复抄写分句或引文。每个 point 返回一项 selection_checks，三个判断均为 true 才可批准；发现疑点或无法确认时相应为 false 并写入 rejections.reason。身份名称沿用原书身份规则，不需要 selection_checks。每个候选 index 恰好进入批准或拒绝一次，完整覆盖。本轮审核说明不成为常驻认识。
capsule_budgets 给出每人每域的完整候选编号、逐项正文字符数与总上限。先如实完成上述语义核验，再从可批准的完整认识中取舍：同一主体同一领域 approved_indices 对应的正文字符数之和不能超过 limit_chars。按认识的代表性和独立问题选取，general 保留整体身份关系、自主选择及已有价值取向，不让最长或最浓烈的关系段落占据全部认识。已核验的旧认识和新增认识共同参与取舍，不能把全部合法候选无条件放入简版。
因为篇幅或重复未选的合法认识，selection_checks 仍如实为 true，rejections.reason 说明完整观点的取舍原因，不谎称来源、上下文或时间无效。完整观点不能放下时整项不选，不剪句、不改写、也不丢掉条件或例外；完整原书照常保留。身份名称不计入领域正文预算。
输出纯 JSON：{"selection_checks":[{"index":1,"context_complete":true,"resident_kind":"recognition","scope_suitable":true,"temporal_supported":true}],"approved_indices":[0,1],"rejections":[{"index":2,"reason":"具体疑点或完整观点取舍原因"}]}。context_complete=false 的检查项额外包含 context_issue。'''

# Search indexes and local storage IDs are not cognition evidence. Full prose,
# ownership, source nature, classifications and temporal receipts stay intact.
_SOURCE_FIELDS = ('n', 'domain', 'name', 'aliases', 'body', 'category', 'subject_id',
                  'knower_id', 'owner_id', 'kind', 'basis', 'scope', 'core', 'source',
                  'state', 'expires_at', 'topics', 'temporal_sections')


class SelectionCheckCoverageError(BookError):
    code = 'selection_check_coverage'
    stage = 'brief_review'


def source_material(rows, *, fragments=False):
    import copy
    material = [{key: copy.deepcopy(row[key]) for key in _SOURCE_FIELDS if key in row}
                for row in rows]
    if fragments:
        from .brief_source_spans import packet
        material = [packet(row) for row in material]
    return material


def _context_receipt(check, candidate):
    """Validate a rejection's source existence, never infer its semantics."""
    if check.get('context_complete') is not False:
        return
    issue = check.get('context_issue')
    if not isinstance(issue, dict):
        raise BookError('认知简版上下文疑点缺少收据')
    kind = issue.get('kind')
    if kind == 'uncertain':
        return
    if kind not in ('missing_qualifier', 'fragment', 'ambiguous_actor'):
        raise BookError('认知简版上下文疑点类型无效')
    indexed = {row['n']: row for row in candidate['sources']}
    # Selection already proves one exact source. No model source substitution;
    # an explicitly supplied reference must still agree with that source.
    n = issue.get('reference', next(iter(indexed)) if len(indexed) == 1 else None)
    quote = issue.get('quote')
    if (type(n) is not int or n not in candidate['references'] or n not in indexed
            or not isinstance(quote, str) or not quote.strip() or quote not in indexed[n]['body']):
        raise BookError('认知简版上下文疑点缺少自身原文')
    if kind == 'missing_qualifier':
        if quote in candidate['text'] or candidate['text'] in quote:
            raise BookError('认知简版遗漏证据不能重复已选内容')
    elif quote not in candidate['text']:
        raise BookError('认知简版断裂或指代证据不在选段中')

# These are textual boundaries, not semantic classification or keyword gates.
_ENDS = '\n。！？；!?;'
_CLOSERS = '”’」』"\''


def _bounded(body, start, end):
    left = body[:start].rstrip(' \t\r')
    right = body[:end].rstrip(' \t\r')
    while left and left[-1] in _CLOSERS:
        left = left[:-1]
    while right and right[-1] in _CLOSERS:
        right = right[:-1]
    return ((not left or left[-1] in _ENDS)
            and (end == len(body) or body[end] in '\r\n'
                 or bool(right and right[-1] in _ENDS)))


def resolve(point, indexed):
    """Take exact text back from its frozen source, never trust a rewritten text."""
    selection = point.get('selection')
    if isinstance(selection, dict) and ('first' in selection or 'last' in selection):
        from .brief_source_spans import select
        selection = select(selection, indexed)
    if not isinstance(selection, dict) or set(selection) != {'reference', 'quote'}:
        raise BookError('认知简版缺少原文选取')
    n, quote = selection['reference'], selection['quote']
    if type(n) is not int or n not in indexed or not isinstance(quote, str) or not quote.strip():
        raise BookError('认知简版原文选取来源无效')
    quote = quote.strip()
    if indexed[n].get('domain') not in ('self', 'other', 'world'):
        raise BookError('认知简版原述性质无效')
    body = indexed[n]['body']
    start = body.find(quote)
    while start >= 0 and not _bounded(body, start, start + len(quote)):
        start = body.find(quote, start + 1)
    if start < 0:
        raise BookError('认知简版选段不是自身来源的完整连续原文')
    text = body[start:start + len(quote)]
    # No lexical pronoun/name replacement: attribution disambiguates the voice.
    return {**point, 'text': text, 'references': [n],
            'selection': {'reference': n, 'quote': text},
            'source_voice': indexed[n]['domain']}


def render(point):
    labels = {'self': '你的自我认识（原述）', 'other': '你对他者的理解（原述）',
              'world': '原书已记录事实'}
    label = labels.get(point.get('source_voice'))
    return f'{label}：{point["text"]}' if label else point['text']


def validate_temporal(point):
    """Only the writer's typed receipt establishes the recent layer here."""
    from .other_facets import split
    from .other_temporal import time_receipt
    overview, facets = split(point['text'])
    receipts = [receipt for text in [overview, *(text for _, text in facets)]
                if (receipt := time_receipt(text.strip()))]
    temporal = point['temporal']
    if receipts:
        if any(receipt['period'] != temporal['period'] for receipt in receipts):
            raise BookError('认知简版选段时间层与原维护收据不一致')
        if temporal['period'] == 'recent' and any(receipt != temporal for receipt in receipts):
            raise BookError('认知简版近期日期与选段自身收据不一致')
    elif temporal['period'] == 'recent':
        raise BookError('认知简版原句的单次日期不构成近期规律收据')


def approved_indices(raw, review_input, approved):
    """Check decisions, not entailment: literal binding no longer needs retyping."""
    checks = raw.get('selection_checks')
    if not isinstance(checks, list):
        raise SelectionCheckCoverageError('认知简版复核检查结构无效')
    points = {c['index']: c for c in review_input['candidates'] if c['kind'] == 'point'}
    if (any(not isinstance(c, dict) or type(c.get('index')) is not int or c['index'] not in points
            for c in checks) or len(checks) != len(points)
            or len({c['index'] for c in checks}) != len(checks)):
        from .diagnostics import emit
        emit('brief_review_invalid', error_code='selection_check_coverage',
             expected_check_count=len(points), actual_check_count=len(checks))
        raise SelectionCheckCoverageError('认知简版复核检查未恰好覆盖观点候选；身份名称不填写观点检查')
    valid = {c['index'] for c in review_input['candidates'] if c['kind'] == 'identity_name'}
    rejected = {item['index'] for item in raw.get('rejections', [])}
    for check in checks:
        candidate = points[check['index']]
        if review_input.get('selection_protocol') == 2:
            try:
                if any(type(check.get(key)) is not bool for key in
                       ('context_complete', 'scope_suitable', 'temporal_supported')):
                    raise BookError('认知简版逐项核验字段缺失或不是布尔值')
                if check.get('resident_kind') not in ('recognition', 'episode', 'mixed', 'uncertain'):
                    raise BookError('认知简版缺少常驻性质核验')
                _context_receipt(check, candidate)
                if check.get('context_complete') is False and check['index'] not in rejected:
                    raise BookError('认知简版上下文疑点缺少拒绝说明')
            except BookError as exc:
                exc.candidate_index = check['index']
                raise
        try:
            resolved = resolve(candidate, {row['n']: row for row in candidate['sources']})
            if (resolved['text'] != candidate['text'] or resolved['references'] != candidate['references']
                    or resolved['source_voice'] != candidate.get('source_voice')):
                continue
        except (BookError, KeyError, TypeError):
            continue
        if (all(check.get(key) is True for key in ('context_complete', 'scope_suitable', 'temporal_supported'))
                and (review_input.get('selection_protocol') != 2 or check['resident_kind'] == 'recognition')):
            valid.add(check['index'])
    return approved & valid


def review_material(review_input):
    """Transmit complete sources once, retaining the exact local binding input."""
    sources, candidates = {}, []
    for candidate in review_input['candidates']:
        for source in candidate['sources']:
            n = source['n']
            if n in sources and sources[n] != source:
                raise BookError('认知简版复核来源版本冲突')
            sources[n] = source
        candidates.append({key: value for key, value in candidate.items()
                           if key not in ('sources', 'selection', 'review_units')})
    from .profile_scopes import CAPSULE_LIMITS
    budgets = {}
    for candidate in candidates:
        if candidate['kind'] != 'point':
            continue
        key = (candidate['subject_id'], candidate['scope'])
        budget = budgets.setdefault(key, {'subject_id': key[0], 'scope': key[1],
            'limit_chars': CAPSULE_LIMITS[key[1]], 'points': []})
        budget['points'].append({'index': candidate['index'], 'chars': len(candidate['text'])})
    return {'selection_protocol': review_input.get('selection_protocol', 1),
            'review_structure': {
                'identity_indices': [c['index'] for c in candidates if c['kind'] == 'identity_name'],
                'point_indices': [c['index'] for c in candidates if c['kind'] == 'point'],
                'selection_check_count': sum(c['kind'] == 'point' for c in candidates)},
            'sources': source_material(list(sources.values())), 'candidates': candidates,
            'capsule_budgets': list(budgets.values())}
