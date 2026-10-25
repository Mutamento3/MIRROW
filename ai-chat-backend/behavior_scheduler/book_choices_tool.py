"""Offer visible quick replies using the normal chat attachment channel."""
import re

from .base_tool import BaseTool, ToolResult, ToolStatus


class BookChoicesTool(BaseTool):
    name = 'book_choices'
    single_use = True
    single_use_success_only = True
    ui_only_result = True
    description = '在主聊天中展示一至三个可点击的快捷回复，选项全文就是人类伙伴点击后发送的话或情境动作。'
    flash_description = '提供主聊天可点击的快捷回复选项'
    pro_guide = (
        '与人类伙伴围绕参考卡互动时，可以用 book_choices 提供一至三个可点击的下一步回复。'
        '这是替人类伙伴拟好的消息：每项从人类伙伴的第一人称出发，‘我’指人类伙伴，‘你’指 AI，'
        '承接 AI 在当前情节中的话和动作。人类伙伴点击后，整项原文作为她的新消息发送，你接着回应。'
        '要提供可点击选项时，通过本次 book_choices 动作提交各项全文。'
        '各项全文在动作块内分别用 <choice> 和 </choice> 标出首尾；'
        '边界内就是实际显示并发送的完整消息，可包含动作、对白和换行。'
        '例如：\n@book_choices\n<choice>我接过你递来的茶，笑着说：“坐这里陪我吧。”</choice>\n'
        '<choice>我向窗外看去：“等雨小一点，我们出去走走。”</choice>\n@'
    )
    parameters_schema = {
        'type': 'object',
        'properties': {'options': {'type': 'array', 'minItems': 1, 'maxItems': 3,
                                   'items': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                                   'description': '完整显示并原样发送的快捷回复文本'}},
        'required': ['options'],
    }

    def get_user_facing_description(self, **kwargs):
        return 'AI 留下了几个快捷回复'

    def compile_action_text(self, instruction):
        if '<choice>' not in instruction and '</choice>' not in instruction:
            return None
        options = re.findall(r'<choice>(.*?)</choice>', instruction, re.DOTALL)
        if len(options) != instruction.count('<choice>') or len(options) != instruction.count('</choice>'):
            # Pass an invalid batch to the existing validator rather than
            # turning an incomplete literal item into a different message.
            return {}
        return {'options': options}

    async def execute(self, **kwargs):
        options = kwargs.get('options')
        if not isinstance(options, list) or not 1 <= len(options) <= 3 or any(not isinstance(text, str) or not text.strip() or len(text) > 500 for text in options):
            return ToolResult(ToolStatus.ERROR, '', error='请提供一至三个完整选项')
        options = [text.strip() for text in options]
        return ToolResult(ToolStatus.SUCCESS, '已展示快捷回复候选：\n' + '\n'.join(options),
                          extra_data={'book_choices': {'options': options}}, delivery='ui_only')
