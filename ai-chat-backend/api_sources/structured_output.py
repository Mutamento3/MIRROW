"""Preserve the full output contract when a GPT source selects prompt JSON."""
from copy import deepcopy
import json


def uses_prompt_schema(source, json_mode, response_format):
    model = source['model']
    return bool(json_mode and model['profile'] == 'gpt-reasoning'
                and model['json_mode'] == 'prompt'
                and source['station']['protocol'] in {'openai-chat', 'openai-responses'}
                and response_format and response_format.get('type') == 'json_schema')


def prompt_schema_messages(source, messages, *, json_mode, response_format):
    """Keep schema restrictions in text and local validation, not native subsets.

    The encoder already embeds this exact schema. Other structured callers get
    one trailing contract carrier, leaving the stable prefix and source order intact.
    """
    result = deepcopy(messages)
    if not uses_prompt_schema(source, json_mode, response_format):
        return result
    schema = json.dumps(response_format['json_schema']['schema'], ensure_ascii=False,
                        separators=(',', ':'))
    if not any(isinstance(m.get('content'), str) and schema in m['content'] for m in result):
        result.append({'role': 'user', 'name': 'mirrow_output_contract',
                       'content': '本轮结构化输出合同（后端协议材料）：\n' + schema})
    return result
