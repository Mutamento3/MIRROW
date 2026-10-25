"""Bounded response counters; never store model text or request materials."""

FIELDS = {'finish_reason': 'TEXT', 'thinking_control': 'TEXT', 'content_chars': 'INTEGER',
          'non_whitespace_chars': 'INTEGER', 'reasoning_chars': 'INTEGER',
          'reasoning_tokens': 'INTEGER', 'requested_max_tokens': 'INTEGER', 'requested_format': 'TEXT'}
REASONS = {'stop', 'length', 'incomplete', 'tool_calls', 'function_call',
           'content_filter', 'completed', 'refusal', 'STOP', 'MAX_TOKENS', 'SAFETY', 'RECITATION'}
CONTROLS = {'disabled', 'enabled', 'none', 'low', 'medium', 'high', 'max', 'unspecified'}


def sanitize(values):
    values = values if isinstance(values, dict) else {}
    safe = {}
    for key in FIELDS:
        value = values.get(key)
        if key == 'finish_reason':
            safe[key] = value if isinstance(value, str) and value in REASONS else None
        elif key == 'thinking_control':
            safe[key] = value if isinstance(value, str) and value in CONTROLS else None
        elif key == 'requested_format':
            safe[key] = value if isinstance(value, str) and value in {'json_object', 'json_schema', 'text', 'application/json'} else None
        else:
            safe[key] = value if type(value) is int and value >= 0 else None
    return safe


def counters(payload, extracted):
    generation = payload.get('generationConfig') or {}
    text = payload.get('text') or {}
    fmt = payload.get('response_format') or text.get('format') or {}
    values = {'thinking_control': 'unspecified',
              'requested_max_tokens': payload.get('max_tokens', payload.get('max_completion_tokens',
                                      payload.get('max_output_tokens', generation.get('maxOutputTokens')))),
              'requested_format': fmt.get('type', generation.get('responseMimeType', 'text'))}
    thinking, reasoning = payload.get('thinking'), payload.get('reasoning')
    if isinstance(thinking, dict):
        values['thinking_control'] = thinking.get('type')
    elif type(payload.get('enable_thinking')) is bool:
        values['thinking_control'] = 'enabled' if payload['enable_thinking'] else 'disabled'
    elif 'reasoning_effort' in payload:
        values['thinking_control'] = payload['reasoning_effort']
    elif isinstance(reasoning, dict):
        values['thinking_control'] = reasoning.get('effort', 'unspecified')
    if extracted is not None:
        content, reasoning = extracted.get('content'), extracted.get('reasoning')
        values.update(finish_reason=extracted.get('finish_reason'),
                      content_chars=len(content) if isinstance(content, str) else None,
                      non_whitespace_chars=sum(not c.isspace() for c in content) if isinstance(content, str) else None,
                      reasoning_chars=len(reasoning) if isinstance(reasoning, str) else None)
        usage = extracted.get('usage')
        if isinstance(usage, dict):
            details = usage.get('completion_tokens_details') or usage.get('output_tokens_details')
            if isinstance(details, dict):
                values['reasoning_tokens'] = details.get('reasoning_tokens')
            elif 'thoughtsTokenCount' in usage:
                values['reasoning_tokens'] = usage['thoughtsTokenCount']
    return sanitize(values)
