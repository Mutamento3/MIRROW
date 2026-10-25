"""Content-free provider completion checks for structured cognition requests."""
import json


class IncompleteOutputError(ValueError):
    pass


def not_dispatched(error):
    """Use explicit transport evidence, never HTTP/error names or missing usage."""
    return getattr(error, 'request_dispatched', None) is False


class ManagedJSONError(json.JSONDecodeError):
    """A completed managed response can use the existing syntax repair budget."""
    def __init__(self):
        super().__init__('模型返回不是完整 JSON 对象', '', 0)


def managed_output_error(result):
    """Translate output failures only; refusal/network/configuration stay terminal."""
    if result.status == 'json_parse_error':
        error = ManagedJSONError()
    elif ((result.status == 'validation_error' and result.error == 'output_incomplete') or
          (result.status == 'empty_content' and result.error == 'model_returned_no_content'
           and result.finish_reason == 'length')):
        error = IncompleteOutputError('模型输出达到长度上限，结构化结果未完成')
    else:
        return None
    error.response = {'content': result.raw_content, 'finish_reason': result.finish_reason}
    error.routing_phase = (result.route, getattr(result, 'phase_id', None))
    return error


def require_complete(response):
    if response.get('finish_reason') == 'length':
        raise IncompleteOutputError('模型输出达到长度上限，结构化结果未完成')


def require_json_object(response, *, empty_array_key=None):
    """Check transport syntax only; each domain still validates its own evidence."""
    content = response.get('content', '').strip()
    if content.startswith('```'):
        content = content.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    value = json.loads(content)
    if value == [] and empty_array_key is not None:
        content = json.dumps({empty_array_key: []})
        value = {empty_array_key: []}
    if not isinstance(value, dict):
        raise json.JSONDecodeError('Expected a JSON object', content, 0)
    return {**response, 'content': content}
