"""
刷手机陪聊 — LLM 生成函数

图片与自然表达共用 visual_dialogue 的输出合同、解析和回退。

上下文由 context.py 的 build_phone_browse_messages() 完整组装（身份/历史/动态事实）。
"""

import logging

from visual_dialogue import generate_visual_dialogue
from routers.phone_browse.context import build_phone_browse_messages
from context_builder.proactive_output import clean_proactive_output

logger = logging.getLogger(__name__)




async def phone_browse_vision_llm(image_b64: str, prompt: str) -> dict:
    """图片+文字一次调用 flash-vision 生成陪刷消息（省一次调用）。

    保留完整上下文，视觉材料与合同后置；回退复用同一请求。
    """
    messages = await build_phone_browse_messages()
    result = await generate_visual_dialogue(messages, [{
        "source_key": "phone_screen", "label": "手机屏幕",
        "image_url": f"data:image/jpeg;base64,{image_b64}",
    }], turn_text=prompt, allow_tools=False)
    if isinstance(result, dict):
        return {"content": clean_proactive_output(result.get("content", "")), "reasoning": result.get("reasoning")}
    return {"content": clean_proactive_output(str(result)), "reasoning": None}
