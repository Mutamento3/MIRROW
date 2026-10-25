"""Resolve only saved Workshop cooperation facts; polling never invokes AI."""


def poll(node):
    from .node_execution_adapter import NodeExecutionResult, NodeExecutionStatus
    from workshop.service import get_service
    from workshop.residency_store import load, ACTIVE
    from workshop.residency import result
    record = load(get_service(), node.side_effect_refs.get('workshop_collaboration_id',''))
    if record and record['state'] in ACTIVE:
        return NodeExecutionResult(NodeExecutionStatus.WAITING_EXTERNAL,
            summary='合作仍在继续，聊天可用；等待实际方案、交付或明确卡点。',
            payload=node.source_payload, side_effect_refs=node.side_effect_refs, retry_safe=False)
    outcome = result(record) if record else {'message':'合作记录暂不可用，未重放原行动。'}
    return NodeExecutionResult(NodeExecutionStatus.SUCCEEDED if record else NodeExecutionStatus.SKIPPED,
        completion_signal='workshop_cooperation_saved' if record else '',
        summary=outcome['message'], payload=outcome, side_effect_refs=node.side_effect_refs, retry_safe=False)
