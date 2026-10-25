"""Opt-in, same-call extraction order; no new ledger or model dispatch.

The candidate suffix is deliberately not selected by ReplayPlanner's date gate.
Frozen v1 inputs and output checkpoints retain their original contract.
"""
from copy import deepcopy

from .state_changes import SUFFIX as STORAGE_SUFFIX

SUFFIX = '+state-extraction-first-v2'
_OLD_RESPONSIBILITY = (
    'A later daily/cross-day process, not this call, decides state and cognition changes.'
)
_RESPONSIBILITY = (
    'This call records explicit source-reported changes and corrections. '
    'Later daily/cross-day processes decide occurrence continuity, frequency and cognition; '
    'they do not replace this call\'s record of what the source actually reported.'
)

# This replaces v1's guidance instead of accumulating another review/checklist.
GUIDANCE = (
    '同一次编码先确定每段经历的主体和来源，再填写state_changes，最后写summary。'
    'state_changes是原话已明确说出的事实变化、纠正、计划或取消，不是你判断的长期状态。'
    '依原话顺序保留会改变这件事理解的具体更新：更正了什么、发生在何时、'
    '对象或部位是什么、最后明确到了哪一步。前后的更正与结果可以各占一项；'
    '归为同一段经历只合并叙述，不抹去其中不同的更新。'
    '先填source_ref和逐字连续quote，再据完整对话填主体、object、aspect、时间与text；'
    '短回答的指代从相邻原话理解，text写成简短、自足、有归属的中文事实。'
    '人类伙伴的变化引用她本人的普通U消息，AI的猜测或复述仍归AI；角色扮演保留虚构归属。'
    'kind用reported、planned、uncertain分别表示已报告、计划和尚未确定。'
    '当时状态用contemporaneous_report，occurred_at和time_expression留空，由后端填报告时刻；'
    '明确过去时间用explicit_expression，time_expression逐字引用本条原话的时间表述，'
    'occurred_at按该条报告日期还原；无法确定则用unresolved并留空occurred_at。'
    '同一方面的不同更新时间分别保留；重复关心、建议和修辞不占变化名额。'
    '每段最多六项，额外的具体更新仍写入summary；没有明确更新时返回空数组。'
    'summary用自然中文归纳经过与最后已知结果，state_changes中的更正和结果与它保持一致。'
    'communication_summaries若在合同中，则只供通信上下文，不替代上述事实记录。'
)


def enabled(prompt_version):
    return SUFFIX[1:] in prompt_version.split('+')


def apply_prompt(system, prompt_version):
    """Replace the conflicting duty statement only for the explicit candidate."""
    if not enabled(prompt_version):
        return system
    if STORAGE_SUFFIX[1:] not in prompt_version.split('+'):
        raise ValueError('state_extraction_requires_state_changes_contract')
    if system.count(_OLD_RESPONSIBILITY) != 1:
        raise ValueError('state_extraction_responsibility_template_changed')
    return system.replace(_OLD_RESPONSIBILITY, _RESPONSIBILITY) + '\n' + GUIDANCE


def order_schema(schema, prompt_version):
    """Order existing fields; preserve their validators, limits and mehuman."""
    if not enabled(prompt_version):
        return schema
    result = deepcopy(schema)
    episode = result['properties']['episodes']['items']
    properties = episode['properties']
    if 'state_changes' not in properties:
        raise ValueError('state_extraction_requires_state_changes_contract')
    priority = ('source_refs', 'primary_subject_id', 'participant_ids', 'state_changes')
    episode['properties'] = {k: properties[k] for k in (*priority, *(k for k in properties if k not in priority))}
    change = episode['properties']['state_changes']['items']
    fields = change['properties']
    first = ('source_ref', 'quote', 'subject_id', 'object', 'aspect', 'kind',
             'temporal_basis', 'time_expression', 'occurred_at', 'text')
    change['properties'] = {k: fields[k] for k in first}
    change['required'] = list(first)
    episode['required'] = [k for k in episode['properties'] if k in episode['required']]
    return result
