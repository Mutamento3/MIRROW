"""Official Flash generation for autonomous tool execution and final NL.

The executor's historical ``call_pro_func`` injection name is retained at
the wiring boundary.  All requests use the metered shared Flash client.
"""

import logging

from llm_client import call_llm_api_flash
from neuron_registry import neuron_trace


logger = logging.getLogger(__name__)


async def generate_self_task_reply(messages):
    with neuron_trace("self_task_executor", recipe="SELF_TASK", model="Flash") as trace:
        try:
            result = await call_llm_api_flash(
                messages, enable_thinking=True, max_attempts=2,
                usage_tag="self_task_executor",
            )
            trace.set_output(str(result.get("content") or "")[:300])
            if result.get("reasoning"):
                trace.set_thinking(str(result["reasoning"])[:300])
            return result
        except Exception as exc:
            trace.set_error(type(exc).__name__)
            logger.warning("Self-task Flash generation failed (%s)", type(exc).__name__)
            return {"content": "", "reasoning": None, "finish_reason": None}
