"""Resume factual extraction with the existing one-repair output budget."""
import json

from . import output_repair
from .provider_contract import IncompleteOutputError
from .raw_output import capture


def validate(raw):
    value = output_repair.parse(raw)
    if not isinstance(value.get('proposals'), list):
        raise output_repair.ContractError('proposal_container_invalid', '提取结果缺少 proposals 列表')
    # Individual proposal and habit semantics retain their existing validators.
    return json.dumps(value, ensure_ascii=False)


async def run(state, prompt, llm, checkpoint):
    if state.get('extraction_output', {}).get('status') in {None, 'not_dispatched'}:
        try:
            await capture(state, 'extraction_output', prompt, llm, checkpoint)
        except (json.JSONDecodeError, IncompleteOutputError):
            pass  # The saved output counts as attempt one, never a new extraction.
    return await output_repair.run(prompt, llm, validate, captured=state['extraction_output'])
