"""Single validated supplier for the frozen source-notes encoding contract."""
import asyncio
import json


async def call(messages, *, schema, max_tokens, audit_path=None, task_id=None):
    from api_sources.config import resolve_chain
    from api_sources.runner import run_structured_once
    sources, timeout = await asyncio.to_thread(resolve_chain, 'memory_encoding')
    sources = [s for s in sources if s['model']['id'] == 'direct_flash']
    # Preserve configured chat transport. Native JSON-schema routing would
    # switch this source to Responses, unlike the accepted request.
    prompt = [dict(m) for m in messages]
    contract = json.dumps(schema, ensure_ascii=False, separators=(',', ':'))
    if not any(contract in m.get('content', '') for m in prompt):
        prompt[-1]['content'] += '\nAuthoritative output schema:\n' + contract
    return await run_structured_once(prompt, temperature=0.0, max_tokens=max_tokens,
        route='memory_encoding', enable_thinking=False, sources=sources,
        timeout_seconds=timeout, response_format=None, stop_on_output_error=True,
        audit_path=audit_path, task_id=task_id)
