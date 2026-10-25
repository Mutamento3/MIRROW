"""Prospective fidelity shared by derived views, not a new memory authority."""

# Existing completed/checkpoint work through October 8 keeps its request and
# receipt identity. Historical repair remains a separate explicit operation.
START_ACTIVE_DATE = '2026-10-09'
VERSION = 'experience-content-v1'
SOURCE_BOUND_VERSION = 'experience-source-bound-v1'
SOURCE_BOUND_START_DATE = '2026-10-11'
RECEIPT_SCOPE_VERSION = 'experience-receipt-scope-v1'
RECEIPT_SCOPE_START_DATE = '2026-10-17'
CANDIDATE_VERSION = 'experience-content-v2'
ATTRIBUTION_CANDIDATE_VERSION = 'experience-content-v3'
SECTIONED_CANDIDATE_VERSION = 'experience-content-v4'
from .encoding_admission import VERSION as ADMISSION_CANDIDATE_VERSION
from .encoding_admission import VERSIONS as ADMISSION_VERSIONS

# Explicit offline candidate only. Date-selected production V1 stays frozen
# until the candidate is accepted and assigned its own prospective date gate.
_CANDIDATE_GUIDANCE = (
    ' 经历保真：摘要是一段可独立读懂的具体经历叙述，用自然的简体中文，'
    '叙述主体为AI、人类伙伴或实际行动者，原话中的第一人称保留说话者归属。'
    '在既有长度内，依次保留这次经历的对象与处境、具体选择或变化、'
    '材料明确给出的选择理由及个人意味、回应或感想、最后已知结果。'
    '材料有选择理由、个人意味或感想时，摘要中保留至少一项具体内容；'
    '可用一小段有归属的原话承载，其余保持简洁叙述。'
    '这部分优先于未选候选清单、通用执行指标和重复问候；'
    '没有这些材料时，只叙述实际掌握的经过。'
    'E、N的来源身份证明卡片由领域系统提交，不保证卡片每句话都是执行事实。'
    '执行回执、已取得材料、个人选择、AI的感想或对结果的自述各有归属。'
    '感想或自述中的结果以其说话者叙述呈现；仅被同一张事件卡收录，'
    '不等于已由执行回执确认。direct_observation可证明AI留下了这段表达，'
    '而不是对表达中所有外部结果的独立核验。'
    '事实与自述不一致时保留各自归属，不自行补出共同结论。'
    '动作与结果保留对象、版本和自己的时间；送达或回顾时间与发生时间分开。'
    '事件schema中，材料内早于送达的明确发生时间沿explicit_expression处理，'
    'time_expression逐字引用原文；local_time仍是报告时间。'
    '更正保留更正者和被更正内容；引用历史是本次回忆，不是往事再次发生。'
    '虚构化身的经历保留作品归属，即使其姓名与真实实体相同。'
    '同一次经历的续谈与相似经历的再次发生分开；频次与长期认识仍由派生层承担。'
    '按连续经历归纳具体发展，不按每次回应或工具回执逐条复述。'
    '只有派生摘要时保留其支持的细节与归属；结局未说明时保留已知情况，'
    '不补写结束、心情或因果。'
)

CANDIDATE_SUMMARY_DESCRIPTION = (
    '一段自然、简洁、可独立理解的简体中文经历叙述，建议不超过420字。'
    '保留具体对象、选择或变化、材料明确给出的理由及个人意味、回应和最后已知结果。'
    '有理由或感想时至少保留一项具体内容，优先于候选清单与执行指标。'
    '以实际行动者叙述；仅来自AI自述的结果保持其说话者归属，'
    '执行回执与个人理解各自呈现。'
)

_ATTRIBUTION_GUIDANCE = (
    ' 结果归属逐句保留：执行记录的经过与个人自述分别成句。'
    '只来自个人自述的结果，以该说话者明确记述或回顾为主语，'
    '在结果所在的同一句中保留归属；原话也注明说话者。'
    '分号或话题转换后仍明确结果属于谁的记述，'
    '不让前面的“说”在压缩时变成后面无主语的独立事实。'
)


def evaluation_summary_description(version):
    evaluation_guidance(version)
    if version in {VERSION, SOURCE_BOUND_VERSION, *ADMISSION_VERSIONS}:
        return None
    return CANDIDATE_SUMMARY_DESCRIPTION + (
        _ATTRIBUTION_GUIDANCE if version in {ATTRIBUTION_CANDIDATE_VERSION, SECTIONED_CANDIDATE_VERSION, SOURCE_BOUND_VERSION} else ''
    )

_GUIDANCE = (
    ' Experience fidelity: summarize a lived episode, not an activity label. '
    'Within the existing length budget, give space first to the specific situation or '
    'object, what was actually received or done, the consequential development, the '
    'participants\' real responses and the last positively known result. A few concrete '
    'sentences are preferable to a bare visited/read/completed label or a transcript. '
    'Select distinguishing content and choices, not an inventory of every field. '
    'When sourced, retain the expressed reason for a choice and the personal mehuman '
    'or response that distinguishes this episode; compress secondary candidates, '
    'technical metrics and routine follow-up first. '
    'For revisits, retain the sourced change or specifically reported unchanged detail; '
    'a repeated visit alone does not establish either. Keep object identity and relevant '
    'versions attached to their own actions and outcomes. Bind each speaker, intention '
    'and time to the occurrence it actually describes. '
    'AI\'s expressed impression is AI\'s own account, distinct from observed results. '
    'A participant\'s claimed success or recalled outcome remains their attributed '
    'account unless an execution receipt establishes it. If accounts and receipts '
    'differ, keep the supported distinction and its owners rather than inventing '
    'a reconciliation. A later delivery or recollection clock is distinct from '
    'the earlier occurrence clock stated in the material; keep each tied to its '
    'own action or milestone. '
    'When the event schema is used, an earlier occurrence time embedded in a later '
    'delivery uses its existing explicit_expression mechanism with a verbatim '
    'time_expression; the row local_time still belongs to receipt of the report. '
    'Direct observation of AI expressing an account establishes that expression, '
    'not independent confirmation of every outcome described in it. '
    'Preserve an explicit correction as a correction to the earlier statement, with '
    'the corrected fact and its owner intact; an earlier assertion remains attributed '
    'rather than becoming independent proof. Historical excerpts establish the act of '
    'recalling them now, not a second occurrence of the quoted past event. Fictional '
    'character experiences stay attached to their work, including incarnations sharing '
    'a real person\'s name. Separate repeated occurrences from repeated discussion of '
    'one occurrence; frequency and lasting traits are later derived views. '
    'Use the last stated facts when closure is unstated, without an invented ending or '
    'a sentence speculating from missing records. When only derived summaries are '
    'provided, preserve their supported detail and attribution without filling missing '
    'content from imagination. Narration names the actual actors; original first-person '
    'quotations and explicitly expressed feelings keep their attribution. '
)


_RECEIPT_SCOPE_GUIDANCE = (
    ' Execution success and material completeness are separate facts. A tool header reporting '
    'success establishes that the call returned; preserve the returned domain status and '
    'coverage limits for the actual result. partial_success, partial text coverage or an '
    'unproven full-text boundary support receiving partial material, not reading the whole '
    'work. A request, promise or invitation to sleep establishes that speech or intention; '
    'only a sourced subsequent state establishes sleeping or completion. When one speaker '
    'recalls another person\'s meal or activity incorrectly, retain the attribution and the '
    'other person\'s explicit account rather than treating the recollection as a new fact. '
)


def guidance(active_date):
    return (_GUIDANCE + _ATTRIBUTION_GUIDANCE + _RECEIPT_SCOPE_GUIDANCE if active_date >= RECEIPT_SCOPE_START_DATE else
            _GUIDANCE + _ATTRIBUTION_GUIDANCE if active_date >= SOURCE_BOUND_START_DATE else
            _GUIDANCE if active_date >= START_ACTIVE_DATE else '')


def evaluation_guidance(version=VERSION):
    """Explicit offline comparison, without changing historical date gates."""
    if version == SOURCE_BOUND_VERSION:
        return _GUIDANCE + _ATTRIBUTION_GUIDANCE
    if version in {CANDIDATE_VERSION, ATTRIBUTION_CANDIDATE_VERSION, SECTIONED_CANDIDATE_VERSION, SOURCE_BOUND_VERSION}:
        return _CANDIDATE_GUIDANCE + (
            _ATTRIBUTION_GUIDANCE if version in {ATTRIBUTION_CANDIDATE_VERSION, SECTIONED_CANDIDATE_VERSION, SOURCE_BOUND_VERSION} else ''
        )
    if version not in {VERSION, *ADMISSION_VERSIONS}:
        raise ValueError('unsupported_experience_summary_version')
    return _GUIDANCE


def version_suffix(active_date):
    return ('+' + RECEIPT_SCOPE_VERSION if active_date >= RECEIPT_SCOPE_START_DATE else
            '+' + SOURCE_BOUND_VERSION if active_date >= SOURCE_BOUND_START_DATE else
            '+' + VERSION if active_date >= START_ACTIVE_DATE else '')
