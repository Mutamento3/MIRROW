"""Foreground access to the same source-bound comment/reply domain."""
import json
from .base_tool import BaseTool, ToolResult, ToolStatus
from .execution_context import get_request_identity


class XiaohongshuInteractionsTool(BaseTool):
    name = 'xiaohongshu_interactions'
    description = ('读取AI绑定账号的小红书评论与艾特，打开原帖评论，预览或发送AI已写好的回复，查询或核验原回复。'
                   'scan只发现通知，read读取原帖文字、可见评论和逐图视觉观察；图片范围与未读部分见回执。'
                   '回复必须绑定已读interaction_id。未知提交只核验原reply_id。')
    flash_description = '忠实提取评论/艾特的扫描、原文读取、预览、回复、状态或原任务核验；不代写回复。'
    pro_guide = ('可以查看自己小红书账号收到的评论和艾特，先读取通知对应的原帖与评论，再决定回复或离开。'
                 'read也会读取原帖图集的可见画面，逐图注明来源、时间、文字和局限；重复读取复用首次观察。'
                 '回复行动写出你的完整原话并沿实际互动回执定位；预览只准备，回复才公开发送。'
                 '通知索引不等于读过原文，平台成功以核验回执为准。提交未知时核验原任务，保留未确认状态。')
    parameters_schema = {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['scan', 'read', 'preview', 'reply', 'status', 'reconcile']},
        'interaction_id': {'type': 'string', 'description': '实际通知/读取回执中的互动ID'},
        'text': {'type': 'string', 'description': 'AI写好的完整回复，最多500 UTF16单位，保留原文换行'},
        'reply_id': {'type': 'string', 'description': '已有回复任务ID，未知时沿原任务查询或核验'}},
        'required': ['action'], 'additionalProperties': False}
    single_use = False

    @property
    def visible_to_pro(self):
        from wander_manager.xhs_interaction_runtime import available
        return available()

    def get_user_facing_description(self, **kwargs):
        return '正在查看或回复小红书互动'

    async def execute(self, action='', interaction_id='', text='', reply_id='', **kwargs):
        from wander_manager.xhs_interaction_runtime import available
        from xhs_interactions.service import request
        sid, _, turn = get_request_identity()
        if not available():
            return ToolResult(ToolStatus.FAILED, '小红书评论与艾特执行器尚不可用。',
                              error='interaction_device_validation_required')
        if kwargs or not sid:
            return ToolResult(ToolStatus.FAILED, '本次互动缺少有效请求身份或参数。', error='invalid_interaction_request')
        try:
            value = await request(session_id=sid, source_key='chat:' + turn if turn else '', action=action,
                                  interaction_id=interaction_id, text=text, reply_id=reply_id)
        except Exception as exc:
            # Never expose raw transport/UI errors or claim that an unknown send failed.
            code = str(exc) if isinstance(exc, ValueError) else 'interaction_unavailable'
            return ToolResult(ToolStatus.FAILED, '小红书互动未完成；已提交的回复应沿原任务查询。', error=code)
        good = value.get('status') in {'observed', 'read', 'previewed', 'published'} or action == 'status'
        from xhs_interactions.context import capture
        return ToolResult(ToolStatus.SUCCESS if good else ToolStatus.FAILED,
                          json.dumps(value, ensure_ascii=False),
                          extra_data={'xhs_interactions': capture(value)},
                          error='' if good else str(value.get('status') or 'interaction_incomplete'))
