"""Complete, source-labelled checkup material for one activity event.

This is a read projection of persisted receipts, not a second observation,
semantic summary or sensory executor. Frozen old snapshots stay unchanged.
"""
from collections.abc import Mapping
from copy import deepcopy
import json

from context_builder.text_utils import _redact_tool_fact
from checkup_orchestrator import grounded_tracking_fact, grounded_tracking_summary


def material(node):
    payload = node.get('source_payload') or {}
    if not isinstance(payload, Mapping) or payload.get('checkup_version') != 1:
        return None
    reflection = str(node.get('reflection') or '')
    return {
        'kind': 'checkup', 'version': 1,
        'source_node_id': str(node.get('node_id') or ''),
        'round_index': node.get('round_index'),
        'started_at': node.get('started_at'),
        'observed_at': payload.get('observed_at'),
        'completed_at': node.get('completed_at'),
        'execution_status': node.get('execution_status'),
        'completion_signal': node.get('completion_signal') or '',
        'intent': str(payload.get('intent') or ''),
        'conclusion': str(payload.get('conclusion') or 'uncertain'),
        **({key: deepcopy(payload[key]) for key in ('source_status', 'goal_assessment') if key in payload}),
        'items': deepcopy(payload.get('checkup_items') or []),
        'selection_errors': deepcopy(payload.get('selection_errors') or []),
        'recorded_reflection': reflection,
        **({'account_source': 'k_node_review'} if (node.get('side_effect_refs') or {}).get('reflection_source') == 'k_node_review' else {}),
        # The runner's exact evidence fallback is reproducible, not a new
        # subjective utterance. Keep its original in the snapshot, render a
        # reference instead of repeating every successful source again.
        'reflection_is_fact_projection': bool(reflection and reflection == grounded_tracking_fact(payload)
            and (node.get('side_effect_refs') or {}).get('reflection_source') != 'k_node_review'),
    }


def independent_settlement(activity, nodes):
    summary = str(activity.get('summary') or '')
    return summary if summary and summary not in {
        grounded_tracking_summary(nodes), str(activity.get('retained_impression') or '')} else ''


def project(value, *, retained_impression=''):
    """Render each persisted source once, retaining errors and uncertainty."""
    clean = lambda text: _redact_tool_fact(str(text or ''))
    stamps = '；'.join(f'{label}={clean(value[key])}' for key, label in (
        ('started_at', '开始'), ('observed_at', '观察记录'), ('completed_at', '节点结束')) if value.get(key))
    parts = [f"查岗节点 {value.get('round_index') or '?'}（来源节点 {clean(value.get('source_node_id'))}）"
             + (f'；{stamps}' if stamps else '；观察时间未记录')
             + f"；执行={clean(value.get('execution_status') or 'unknown')}；来源状态={clean(value.get('source_status') or value.get('conclusion') or 'uncertain')}。"]
    if value.get('intent'):
        parts.append('AI 当时的信息缺口：' + clean(value['intent']))
    from checkup_assessment import goal_assessment_text
    assessment = goal_assessment_text(value.get('goal_assessment'))
    if assessment:
        parts.append(assessment)
    items = value.get('items') or []
    if not items:
        parts.append('本次没有取得可核验的感知回执，人类伙伴当时的活动未知。')
    for item in items:
        source = clean(item.get('description') or item.get('tool') or '感知来源')
        tool = clean(item.get('tool'))
        line = f"{source}" + (f'（{tool}）' if tool and tool != source else '')
        line += '：状态=' + clean(item.get('status') or 'unknown')
        flags, labels = [], []
        for key, label in (('attempted', '尝试'), ('captured', '采集'), ('analyzed', '分析')):
            if key in item:
                labels.append(label)
                flags.append({True: '是', False: '否'}.get(item[key], '未知'))
        if flags:
            line += '；' + '/'.join(labels) + '=' + '/'.join(flags)
        parts.append(line)
        if item.get('result'):
            parts.append('来源回执：' + clean(item['result']))
        if item.get('error'):
            parts.append('失败／不确定原因：' + clean(item['error']))
        extra = {key: item[key] for key in item if key not in {
            'tool', 'description', 'status', 'attempted', 'captured', 'analyzed', 'result', 'error'}}
        if extra:
            parts.append('来源补充：' + clean(json.dumps(extra, ensure_ascii=False)))
    if value.get('selection_errors'):
        parts.append('感官选择未执行原因：' + clean(json.dumps(value['selection_errors'], ensure_ascii=False)))
    reflection = str(value.get('recorded_reflection') or '')
    if (reflection and not value.get('reflection_is_fact_projection')
            and not value.get('reflection_is_retained') and reflection != retained_impression):
        parts.append('AI 当时节点感想（原述）：' + clean(reflection))
    return '\n'.join(parts)
