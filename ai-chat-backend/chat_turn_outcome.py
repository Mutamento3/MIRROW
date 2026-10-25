"""Body-free terminal receipts shared by HTTP and WebSocket chat admission."""
FAILURE_REASONS = frozenset({'all_sources_unavailable', 'model_refusal'})

def failure_reason(result) -> str:
    reason = getattr(result, 'failure_reason', '')
    return reason if reason in FAILURE_REASONS else ''

def finalize_chat_turn(guard, token, result, *, session_id='', turn_id='', persisted=True) -> str:
    """Call after durable message/tool persistence, before sending the receipt."""
    if failure_reason(result) or not persisted:
        guard.fail(token)
        return 'failed'
    guard.complete(token)
    return 'completed'

def needs_tool_receipt(result, final_response, intermediate_messages, tool_calls) -> bool:
    """Real completed tools need a durable carrier even without any AI speech."""
    return bool(failure_reason(result) and tool_calls and (not intermediate_messages) and (not str(final_response or '').strip()))
