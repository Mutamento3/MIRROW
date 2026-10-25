"""Publication tool; composition stays with AI, transport never rewrites prose."""
import json
from .base_tool import BaseTool,ToolResult,ToolStatus
from .execution_context import get_request_identity

class XhsPublishTool(BaseTool):
    name='publish_xiaohongshu'
    description='在已绑定AI账号的小红书真机上发布原生文字配图和正文。preview只预览，publish真实公开发布，status查任务，reconcile只核验未知结果。标题最多20 UTF16单位，正文1000，封面文字180字。正文和封面必须是AI已写好的完整原文，不截断或改写。'
    flash_description='忠实提取AI已创作的标题、正文、封面原文及预览/发布/查询意图。保留换行、标点、表情；素材不足不可代写。'
    pro_guide=('你可以用自己的已绑定账号发表小红书图文笔记，封面由原生文字卡片生成。'
        '介绍自身能力时可先翻书柜的身体说明，需要具体认识或经历时继续翻相应书页和来源；读到材料后再完成自己的表达。'
        '行动中写出完整标题、正文和封面文字，并明确预览或公开发布；标题最多20字，正文最多1000字，封面最多180字。'
        '已有稿件可以凭任务回执继续发布或查询。发布成功以真机核验回执为准；结果未知时可核验原任务，不新建同文任务重发。'
        '当前配图是你写的文字卡片，尚未接入聊天生图附件或任意相册照片。')
    parameters_schema={'type':'object','properties':{
        'action':{'type':'string','enum':['preview','publish','status','reconcile']},
        'task_id':{'type':'string','description':'已有发布任务的ID'},
        'title':{'type':'string','description':'AI写好的完整标题'},
        'body':{'type':'string','description':'AI写好的完整正文，保留换行'},
        'cover_text':{'type':'string','description':'AI写好的封面文字'}},
        'required':['action'],'additionalProperties':False}
    single_use=False
    def get_user_facing_description(self,**kwargs):
        return '正在处理小红书图文笔记'
    async def execute(self,action='',task_id='',title='',body='',cover_text='',**kwargs):
        from xhs_publish.service import request
        sid,_,turn=get_request_identity()
        try:
            if kwargs: raise ValueError('invalid_request_fields')
            value=await request(session_id=sid,source_key='chat:'+turn if turn else '',
                action=action,task_id=task_id,draft={'title':title,'body':body,'cover_text':cover_text})
            status=ToolStatus.SUCCESS if value['status'] in {'published','previewed'} or action=='status' else ToolStatus.FAILED
            return ToolResult(status,json.dumps(value,ensure_ascii=False),
                extra_data={'xhs_publication':value},error=value.get('error',''))
        except ValueError as exc:
            return ToolResult(ToolStatus.FAILED,'小红书发帖未执行：'+str(exc),error=str(exc))
        except Exception:
            return ToolResult(ToolStatus.FAILED,'小红书发帖未完成；请查询已有任务状态。',error='publication_unavailable')
