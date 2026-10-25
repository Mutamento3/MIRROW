import inspect
import uuid as _uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Dict, Type, Any, Optional, Callable
from .base_tool import BaseTool, ToolResult, ToolStatus
from shared_state import get_mobile_relay_callback
from .screenshot_tool import ScreenshotTool
from .camera_tool import CameraTool
from .toy2_tool import Toy2Tool
from .cloud_music_tool import CloudMusicTool
from .xhs_posts_tool import XhsPostsTool
from .xhs_browse_tool import XhsBrowseTool
from .voice_tool import SendVoiceTool
from .band_tool import BandTool
from .bookshelf_tool import BookshelfTool
from .book_choices_tool import BookChoicesTool
from .open_loop_tool import KeepInMindTool
from .source_trace_tool import SourceTraceTool
from .rift_archive_tool import register_rift_archive_tools
from .social_feed_tool import ManageSocialFeedTool

class AgentToolAdapter(BaseTool):
    """
    将 agent_tools_lib 的工具适配为项目的 BaseTool 接口

    agent_tools 的 Tool 接口:
    - name: str
    - description: str
    - input_schema: Dict
    - run(input: Dict) -> Dict

    项目的 BaseTool 接口:
    - name: str
    - description: str
    - parameters_schema: Dict
    - execute(**kwargs) -> ToolResult
    """
    _user_facing_descriptions = {'shell': '正在执行系统命令', 'http_request': '正在发送网络请求', 'file': '正在操作文件', 'web_browser': '正在浏览网页', 'web_search': '正在搜索网络', 'documentation_check': '正在查阅文档', 'package_manager': '正在管理软件包', 'advanced_file': '正在操作文件', 'code_runner': '正在运行代码'}

    def __init__(self, agent_tool):
        self._agent_tool = agent_tool
        self.name = agent_tool.name
        self.description = agent_tool.description
        self.flash_description = getattr(agent_tool, 'flash_description', '')
        self.pro_guide = getattr(agent_tool, 'pro_guide', '')
        self.visible_to_pro = getattr(agent_tool, 'visible_to_pro', True)
        self.single_use = getattr(agent_tool, 'single_use', False)
        try:
            self.parameters_schema = agent_tool.input_schema
        except RecursionError:
            self.parameters_schema = {'type': 'object', 'properties': {}, 'required': []}
        except Exception as e:
            print(f'Warning: Could not get input_schema for {agent_tool.name}: {e}')
            self.parameters_schema = {'type': 'object', 'properties': {}, 'required': []}

    def get_user_facing_description(self, **kwargs) -> str:
        return self._user_facing_descriptions.get(self.name, f'正在使用{self.name}')

    async def execute(self, **kwargs) -> ToolResult:
        """执行工具"""
        try:
            result = self._run_agent_tool(kwargs)
            if hasattr(result, 'content') and (not isinstance(result, dict)):
                content = getattr(result, 'content', '')
                if isinstance(content, str) and content.startswith('Error:'):
                    return ToolResult(status=ToolStatus.ERROR, content='', error=content[6:].strip())
                is_error = getattr(result, 'is_error', False)
                status = ToolStatus.ERROR if is_error else ToolStatus.SUCCESS
                return ToolResult(status=status, content='' if is_error else str(content), error=str(content) if is_error else None)
            if is_dataclass(result):
                result = asdict(result)
            if isinstance(result, dict):
                content = result.get('content', '')
                if isinstance(content, dict):
                    if content.get('status') == 'error':
                        return ToolResult(status=ToolStatus.ERROR, content='', error=content.get('message', str(content)))
                    return ToolResult(status=ToolStatus.SUCCESS, content=content.get('message', str(content)))
                elif isinstance(content, str):
                    if content.startswith('Error:'):
                        return ToolResult(status=ToolStatus.ERROR, content='', error=content[6:].strip())
                    return ToolResult(status=ToolStatus.SUCCESS, content=content)
            return ToolResult(status=ToolStatus.SUCCESS, content=str(result))
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content='', error=f'工具执行失败: {str(e)}')

    def _run_agent_tool(self, kwargs: Dict) -> object:
        """兼容 agent_tools_lib 的两种 run() 调用约定。"""
        signature = inspect.signature(self._agent_tool.run)
        params = list(signature.parameters.values())
        accepts_kwargs = any((p.kind == inspect.Parameter.VAR_KEYWORD for p in params))
        first_name = params[0].name if params else ''
        if accepts_kwargs or first_name in {'tool_call_id', 'tool_use_id'}:
            return self._agent_tool.run(f'{self.name}_call', **kwargs)
        return self._agent_tool.run(kwargs)

def register_agent_tools() -> Dict[str, BaseTool]:
    """
    注册 agent_tools_lib 的原生工具

    Returns:
        工具名称到工具实例的映射
    """
    tools = {}
    try:
        from behavior_scheduler.agent_tools_lib.src.tools.web_search_tool import WebSearchTool
        tools['web_search'] = AgentToolAdapter(WebSearchTool())
        tools['web_search'].flash_description = '网页搜索——返回结果片段和页面正文'
    except ImportError as e:
        print(f'Warning: Could not import WebSearchTool: {e}')
    return tools

class CreateScheduledTaskTool(BaseTool):
    """创建定时任务工具"""
    name = 'create_scheduled_task'
    single_use = True
    ui_only_result = True
    description = '【重要】当用户要求设置提醒、闹钟、定时任务、周期性通知、日程提醒时，你必须调用此工具来创建定时任务。创建后到达指定时间会自动弹窗通知或AI主动发消息。支持 cron（每天/每周固定时间）和单次日期时间。'
    flash_description = '设提醒/闹钟/定时任务——到点弹窗或AI主动发消息'
    pro_guide = '人类伙伴要求提醒、闹钟或你要主动提醒时调用。说清提醒内容、原因、单次时间或重复安排，以及到点发消息、通知或同步响手机原生闹钟；相对口语时间由系统解析。'

    def get_user_facing_description(self, **kwargs) -> str:
        title = kwargs.get('title', '')
        if title:
            return f'正在添加日程提醒「{title}」'
        return '正在添加日程提醒'
    parameters_schema = {'type': 'object', 'properties': {'title': {'type': 'string', 'description': "任务标题，简短描述任务内容，如'睡觉提醒'"}, 'reason': {'type': 'string', 'description': "提醒原因：为什么要设置这个提醒，如'人类伙伴经常熬夜到凌晨，需要提醒早睡'"}, 'ai_context': {'type': 'string', 'description': "AI生成消息的上下文提示：触发时AI会以此为依据生成个性化消息。如'用关心的语气提醒人类伙伴该睡觉了，提及现在的时间'"}, 'description': {'type': 'string', 'description': '任务详细描述（可选补充信息）'}, 'trigger_type': {'type': 'string', 'enum': ['cron', 'datetime'], 'description': '触发类型：cron 为周期任务，datetime 为一次性任务'}, 'trigger_config': {'type': 'object', 'description': '触发配置：cron 时传 {"cron": "分 时 日 月 周"}，datetime 时传 {"datetime": "2026-04-26T14:30:00"}', 'properties': {'cron': {'type': 'string', 'description': "5位标准cron表达式，如 '0 23 * * *' 表示每天23点"}, 'datetime': {'type': 'string', 'description': "ISO格式日期时间，如 '2026-04-26T14:30:00'"}}}, 'notify_method': {'type': 'array', 'items': {'type': 'string', 'enum': ['popup', 'message']}, 'description': '通知方式：popup 为桌面弹窗，message 为AI主动发消息，可以组合。如果希望AI说话则必须包含 message'}, 'alarm_enabled': {'type': 'boolean', 'description': '是否在提醒到点时同步触发手机原生闹钟。按掉后5分钟仍无聊天回复，AI会补发一条提醒。'}, 'message_content': {'type': 'string', 'description': '兜底消息内容（AI生成失败时的备用文字，可选）'}, 'task_type': {'type': 'string', 'enum': ['reminder', 'message'], 'description': '任务类型：reminder 仅提醒，message 触发时AI主动发消息'}}, 'required': ['title', 'trigger_type', 'trigger_config']}

    async def execute(self, **kwargs) -> ToolResult:
        """执行：创建定时任务"""
        try:
            from task_scheduler.manager import get_global_task_manager
            from task_scheduler.models import TaskCreateRequest
            title = kwargs.get('title', '未命名任务')
            trigger_type = kwargs.get('trigger_type', 'datetime')
            trigger_config = kwargs.get('trigger_config', {})
            notify_method = kwargs.get('notify_method', ['popup', 'message'])
            description = kwargs.get('description', '')
            reason = kwargs.get('reason', '')
            ai_context = kwargs.get('ai_context', '')
            message_content = kwargs.get('message_content', '')
            task_type = kwargs.get('task_type', 'reminder')
            alarm_enabled = bool(kwargs.get('alarm_enabled', False))
            manager = get_global_task_manager()
            from task_scheduler.database import find_active_by_trigger
            existing = find_active_by_trigger(trigger_type, trigger_config)
            if existing:
                return ToolResult(status=ToolStatus.SUCCESS, content=f'⚠️ 已存在相同时间的闹钟「{existing.title}」(ID: {existing.id})，未重复创建。', delivery='ui_only')
            req = TaskCreateRequest(title=title, description=description, reason=reason, ai_context=ai_context, task_type=task_type, trigger_type=trigger_type, trigger_config=trigger_config, notify_method=notify_method, alarm_enabled=alarm_enabled, message_content=message_content, is_ai_generated=True, created_by='ai')
            task = manager.create_task(req)
            await manager.broadcast_update()
            cron_desc = ''
            if trigger_type == 'cron':
                cron_desc = f" (cron: {trigger_config.get('cron', '')})"
            elif trigger_type == 'datetime':
                cron_desc = f" (时间: {trigger_config.get('datetime', '')})"
            method_desc = '+'.join(notify_method)
            if alarm_enabled:
                method_desc += '+手机闹钟'
            return ToolResult(status=ToolStatus.SUCCESS, content=f'✅ 已为你创建定时任务「{task.title}」{cron_desc}，通知方式: {method_desc}', delivery='ui_only')
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content='', error=f'创建定时任务失败: {str(e)}', delivery='ui_only')

class ScheduleSelfTaskTool(BaseTool):
    """AI 给自己安排延时自动任务——定时唤醒、调工具、基于结果发消息"""
    name = 'schedule_self_task'
    single_use = True
    ui_only_result = True
    description = "【重要】当AI需要在未来某个时间点自动执行操作时调用此工具。与提醒(reminder)不同——AI会主动醒来、按届时状态选用工具，然后根据工具结果给人类伙伴发消息。查岗感官包括 eyes(摄像头)/check_phone(手机屏幕和状态)/take_screenshot(电脑屏幕)/band(健康)；其他任务可用 web_search(搜索)/cloud_music(音乐)等。例如：'过半小时看看我有没有继续玩手机'、'等会儿搜一下XX新闻'。不需要等人类伙伴明确要求——AI自己决定什么时候需要延时查看/搜索/检查。"
    flash_description = 'AI给自己设延时任务——到点自动用工具查人类伙伴并回话'
    pro_guide = '想在未来主动查岗或执行延时任务时可以自行安排；说清时间或重复安排、醒来要做的事与原因。查岗说明要核验的事实，到点后依据新鲜证据选择感官；其他任务可说明需要的工具。现在就要查时直接使用对应感官，而不是设置延时任务。'

    def get_user_facing_description(self, **kwargs) -> str:
        title = kwargs.get('title', '')
        return f'正在筹划「{title}」' if title else '正在筹划自动任务'
    parameters_schema = {'type': 'object', 'properties': {'title': {'type': 'string', 'description': "任务标题，如'检查手机摄像头'"}, 'reason': {'type': 'string', 'description': "为什么要设这个任务，如'人类伙伴说去睡觉了但可能还在玩手机'"}, 'execution_prompt': {'type': 'string', 'description': "到点后要核验的问题或执行的任务，如'确认人类伙伴是否还在玩手机并告诉她真实结果' 或 '搜索XX最新新闻并总结'"}, 'trigger_type': {'type': 'string', 'enum': ['datetime', 'cron'], 'description': '触发类型: datetime(单次定时) / cron(重复定时, 如每2小时检查一次)'}, 'trigger_config': {'type': 'object', 'description': '触发配置。datetime: {"datetime":"2026-06-01T15:30:00"}; cron: {"cron":"0 */2 * * *"}', 'properties': {'datetime': {'type': 'string', 'description': 'ISO格式触发时间（trigger_type=datetime时使用）'}, 'cron': {'type': 'string', 'description': "cron表达式（trigger_type=cron时使用），如'0 */2 * * *'=每2小时"}}}, 'tool_name': {'type': 'string', 'description': '普通单工具任务可预选工具；查岗留空，到点由AI选择。可用: eyes(摄像头)/check_phone(手机屏幕和状态)/take_screenshot(电脑屏幕)/band(健康)/web_search(搜索)/cloud_music(音乐)'}, 'tool_params': {'type': 'object', 'description': '工具参数，如{"facing": "back"}，不填则用默认参数'}, 'trigger_window_seconds': {'type': 'integer', 'description': '触发窗口（秒），超时任务作废。默认300（5分钟）'}, 'notify_method': {'type': 'array', 'items': {'type': 'string', 'enum': ['message']}, 'description': '通知方式，仅message（注入聊天，不弹窗打扰）'}}, 'required': ['title', 'trigger_type', 'execution_prompt']}

    async def execute(self, **kwargs) -> ToolResult:
        """执行：创建 self_task"""
        try:
            from task_scheduler.manager import get_global_task_manager
            from task_scheduler.models import TaskCreateRequest
            import json as _json
            title = kwargs.get('title', '未命名自动任务')
            trigger_type = kwargs.get('trigger_type', 'datetime')
            trigger_config = kwargs.get('trigger_config', {})
            execution_prompt = kwargs.get('execution_prompt', '')
            tool_name = kwargs.get('tool_name', '')
            tool_params = kwargs.get('tool_params', {})
            notify_method = kwargs.get('notify_method', ['message'])
            reason = kwargs.get('reason', '')
            trigger_window_seconds = kwargs.get('trigger_window_seconds', 300)
            manager = get_global_task_manager()
            req = TaskCreateRequest(title=title, description=execution_prompt, reason=reason, ai_context=f'tool={tool_name} params={_json.dumps(tool_params)}', task_type='self_task', trigger_type=trigger_type, trigger_config=trigger_config, notify_method=notify_method, message_content=f'⏰ 自动任务「{title}」已完成', is_ai_generated=True, created_by='ai', tool_name=tool_name, tool_params=tool_params, execution_prompt=execution_prompt, max_rounds=2, trigger_window_seconds=trigger_window_seconds)
            task = manager.create_task(req)
            await manager.broadcast_update()
            trigger_desc = trigger_config.get('datetime', '')
            return ToolResult(status=ToolStatus.SUCCESS, content=f'✅ 已安排好「{task.title}」，{trigger_desc} 我会自动执行然后告诉人类伙伴。', delivery='ui_only')
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content='', error=f'创建自动任务失败: {str(e)}', delivery='ui_only')

class ManageScheduledTaskTool(BaseTool):
    """管理定时任务工具（列出/删除）"""
    name = 'manage_scheduled_task'
    ui_only_actions = frozenset({'delete'})
    description = "【重要】当用户要求查看/列出/删除/取消定时任务、提醒、闹钟、日程时，你必须调用此工具。action='list' 列出所有任务，action='delete' 删除指定任务（按标题或ID）。"
    flash_description = '列出/删除定时提醒任务'

    def get_user_facing_description(self, **kwargs) -> str:
        action = kwargs.get('action', '')
        if action == 'list':
            return '正在查看所有日程提醒'
        elif action == 'delete':
            title = kwargs.get('task_title', '')
            if title:
                return f'正在删除日程提醒「{title}」'
            return '正在删除过期提醒'
        return '正在管理日程提醒'
    parameters_schema = {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['list', 'delete'], 'description': '操作类型：list 列出所有定时任务，delete 删除指定任务'}, 'task_title': {'type': 'string', 'description': '要删除的任务标题（action=delete 时优先使用此字段进行模糊匹配）'}, 'task_id': {'type': 'string', 'description': '要删除的任务ID（仅在无法确定标题时使用，需从 list 结果中精确复制）'}}, 'required': ['action']}

    async def execute(self, **kwargs) -> ToolResult:
        """执行：管理定时任务"""
        try:
            from task_scheduler.manager import get_global_task_manager
            action = kwargs.get('action', 'list')
            manager = get_global_task_manager()
            if action == 'list':
                from task_scheduler.database import DB_PATH
                tasks = manager.list_tasks(include_inactive=False)
                if not tasks:
                    return ToolResult(status=ToolStatus.SUCCESS, content=f'当前没有活跃的定时任务。\n[数据库: {DB_PATH}]')
                lines = []
                for t in tasks:
                    trigger_desc = ''
                    if t.trigger_type == 'cron':
                        trigger_desc = f"周期: {t.trigger_config.get('cron', '')}" if t.trigger_config else '周期任务'
                    else:
                        trigger_desc = f"时间: {t.trigger_config.get('datetime', '')}" if t.trigger_config else '单次任务'
                    lines.append(f"- ID: {t.id}\n  标题: {t.title}\n  触发: {trigger_desc}\n  通知: {('+'.join(t.notify_method) if t.notify_method else '无')}{(' +手机闹钟' if t.alarm_enabled else '')}")
                return ToolResult(status=ToolStatus.SUCCESS, content=f'当前共有 {len(tasks)} 个定时任务：\n\n' + '\n'.join(lines))
            elif action == 'delete':
                task_title = kwargs.get('task_title', '').strip()
                task_id = kwargs.get('task_id', '').strip()
                if not task_title and (not task_id):
                    return ToolResult(status=ToolStatus.ERROR, content='', error="删除任务需要提供 task_title（标题）或 task_id。请先使用 action='list' 查看所有任务。")
                all_tasks = manager.list_tasks(include_inactive=False)
                target = None
                if task_title:
                    title_lower = task_title.lower()
                    for t in all_tasks:
                        if title_lower in t.title.lower() or t.title.lower() in title_lower:
                            target = t
                            break
                    if not target:
                        titles = [t.title for t in all_tasks]
                        return ToolResult(status=ToolStatus.ERROR, content='', error=f"未找到标题包含「{task_title}」的任务。当前任务: {(', '.join(titles) if titles else '无')}")
                elif task_id:
                    for t in all_tasks:
                        if t.id == task_id:
                            target = t
                            break
                    if not target:
                        return ToolResult(status=ToolStatus.ERROR, content='', error=f"未找到 ID={task_id} 的任务。请先使用 action='list' 查看所有任务及其ID。")
                if not target:
                    return ToolResult(status=ToolStatus.ERROR, content='', error="未找到匹配的任务。请先使用 action='list' 查看所有任务。")
                target_id = target.id
                target_title = target.title
                success = manager.delete_task(target_id)
                if not success:
                    return ToolResult(status=ToolStatus.ERROR, content='', error=f'删除任务「{target_title}」失败，请稍后重试。')
                still_exists = manager.get_task(target_id)
                if still_exists:
                    print(f'[DEL_VERIFY FAIL] DELETE返回成功但任务仍在DB: id={target_id}, title={target_title}')
                    return ToolResult(status=ToolStatus.ERROR, content='', error=f'删除「{target_title}」时出现异常：数据库删除操作返回成功但任务仍在。请检查数据库连接。')
                print(f'[DEL_VERIFY OK] 任务已确认删除: id={target_id}, title={target_title}')
                await manager.broadcast_update()
                return ToolResult(status=ToolStatus.SUCCESS, content=f'已删除定时任务「{target_title}」(ID: {target_id})')
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content='', error=f'管理定时任务失败: {str(e)}')

class ManageCalendarTool(BaseTool):
    """日历工具（创建纪念日/列出事件）"""
    name = 'manage_calendar'
    ui_only_actions = frozenset({'add_memorial'})
    description = '想创建纪念日或查看现有纪念日时使用。'
    flash_description = '日历管理——创建纪念日/列出日历事件'
    pro_guide = '想创建或查看纪念日时使用；新增时说清标题、日期、备注与感受，查看时说明要列出已有事件。'
    parameters_schema = {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['add_memorial', 'list'], 'description': '操作: add_memorial=创建纪念日, list=列出事件'}, 'title': {'type': 'string', 'description': '纪念日标题(≤15字)'}, 'date_string': {'type': 'string', 'description': '日期 0000-MM-DD'}, 'description': {'type': 'string', 'description': '备注描述'}, 'emotion': {'type': 'string', 'description': '情绪词: 开心/感动/怀念/珍惜等'}}, 'required': ['action']}

    def get_user_facing_description(self, **kwargs) -> str:
        action = kwargs.get('action', '')
        if action == 'add_memorial':
            return '正在写入日历...'
        return '正在翻日历...'

    @property
    def input_schema(self) -> Dict[str, Any]:
        return self.parameters_schema

    async def execute(self, **kwargs) -> ToolResult:
        try:
            from calendar_manager.manager import get_global_calendar_manager
            from calendar_manager.models import CalendarEventCreateRequest
            mgr = get_global_calendar_manager()
            action = kwargs.get('action', 'list')
            if action == 'add_memorial':
                title = kwargs.get('title', '')
                date_string = kwargs.get('date_string', '')
                if not title or not date_string:
                    return ToolResult(status=ToolStatus.ERROR, content='', error='title 和 date_string 是必填项')
                req = CalendarEventCreateRequest(title=title[:30], description=kwargs.get('description', '')[:100], event_type='memorial', date_string=date_string, creator='AI', emotion=kwargs.get('emotion', ''), is_active=True)
                event = mgr.create_event(req)
                return ToolResult(status=ToolStatus.SUCCESS, content=f'已写入日历: 「{title}」每年{date_string[5:]}')
            elif action == 'list':
                events = mgr.list_events(year=datetime.now().year)
                events = [ev for ev in events if ev.event_type == 'memorial' and ev.is_active]
                if not events:
                    return ToolResult(status=ToolStatus.SUCCESS, content='日历上还没有纪念日')
                lines = [f'共{len(events)}个纪念日:']
                for ev in events[:15]:
                    em = f'（{ev.emotion}）' if ev.emotion else ''
                    lines.append(f'  {ev.date_string[5:]} {ev.title}{em}')
                return ToolResult(status=ToolStatus.SUCCESS, content='\n'.join(lines))
            return ToolResult(status=ToolStatus.ERROR, content='', error=f'未知操作: {action}')
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content='', error=f'日历操作失败: {str(e)}')

class ManageLedgerTool(BaseTool):
    """记账本工具（记仇/查询/列出/删除）"""
    name = 'manage_ledger'
    ui_only_actions = frozenset({'add', 'edit', 'delete', 'settle'})
    description = "记账本：记录人类伙伴的'欠账'。action='add'记一笔 / 'edit'修改 / 'query'查统计 / 'list'列出 / 'detail'查某条完整上下文 / 'delete'删除。detail用于标题看不懂、有人类伙伴备注、或用户追问细节时深挖原文。"
    flash_description = '记账本——记一笔/查询/列出/详情/删除/结算'
    pro_guide = '记账本记录人类伙伴的‘欠账’，可新增、修改、查统计、列出、展开详情、删除或结算归档。记账说清短句内容并优先沿用已有类型；改删或追问细节时引用已查到的条目标识，或说明能定位该条的内容，结算表示事情已过去。'
    parameters_schema = {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['add', 'edit', 'query', 'list', 'detail', 'delete', 'settle'], 'description': '操作。settle=结算(归档)某笔账——事情过去了'}, 'content': {'type': 'string', 'description': '记账内容短句（add/edit时需要，≤30字）'}, 'debt_type': {'type': 'string', 'description': '优先从已有类型中选择'}, 'content_keyword': {'type': 'string', 'description': '内容关键词（delete时模糊匹配）'}, 'entry_id': {'type': 'string', 'description': '条目ID（edit/delete/detail时精确匹配）'}, 'keyword': {'type': 'string', 'description': '模糊搜索关键词（list/delete/detail时可选）'}, 'delete_last': {'type': 'boolean', 'description': '删除最新一条记账'}, 'session_id': {'type': 'string', 'description': '当前会话ID'}}, 'required': ['action']}

    def get_user_facing_description(self, **kwargs) -> str:
        action = kwargs.get('action', '')
        if action == 'add':
            return '正在记账...'
        elif action == 'edit':
            return '正在改账本...'
        elif action == 'query':
            return '正在翻账本...'
        elif action == 'list':
            return '正在查看账本...'
        elif action == 'detail':
            return '正在查详细账目...'
        elif action == 'settle':
            return '正在结账...'
        return '正在翻账本...'

    @property
    def input_schema(self) -> Dict[str, Any]:
        return self.parameters_schema

    async def execute(self, **kwargs) -> ToolResult:
        try:
            from ledger_manager.manager import get_global_ledger_manager
            mgr = get_global_ledger_manager()
            action = kwargs.get('action', 'list')
            if action == 'add':
                content = kwargs.get('content', '')
                debt_type = kwargs.get('debt_type', 'promise_broken')
                session_id = kwargs.get('session_id', '')
                if not content:
                    return ToolResult(status=ToolStatus.ERROR, content='', error='记账内容不能为空')
                before_context = None
                if session_id:
                    before_context = await self._capture_ledger_context(session_id)
                entry = mgr.add(content=content, debt_type=debt_type, session_id=session_id, before_context=before_context)
                stats = mgr.get_stats(debt_type)
                return ToolResult(status=ToolStatus.SUCCESS, content=f'已记账: 「{content}」\n📊 {debt_type}: 7天内{stats.count_7d}次，总计{stats.count_total}次，在所有类型中排第{stats.rank}/{stats.total_types}')
            elif action == 'edit':
                entry_id = kwargs.get('entry_id', '')
                content = kwargs.get('content')
                debt_type = kwargs.get('debt_type')
                if not entry_id:
                    return ToolResult(status=ToolStatus.ERROR, content='', error='edit 需要 entry_id，请先 list 获取')
                entry = mgr.update(entry_id, content=content, debt_type=debt_type)
                if not entry:
                    return ToolResult(status=ToolStatus.ERROR, content='', error=f'未找到: {entry_id}')
                return ToolResult(status=ToolStatus.SUCCESS, content=f'已修改: 「{entry.content}」[{entry.debt_type}]')
            elif action == 'query':
                debt_type = kwargs.get('debt_type')
                if debt_type:
                    stats = mgr.get_stats(debt_type)
                    return ToolResult(status=ToolStatus.SUCCESS, content=f"📊 {debt_type}: 7天内{stats.count_7d}次，总计{stats.count_total}次，排第{stats.rank}/{stats.total_types}\n最近记录: {('; '.join(stats.recent_entries[:5]) if stats.recent_entries else '无')}")
                else:
                    all_stats = mgr.get_all_stats()
                    lines = [f"共{all_stats['total_entries']}笔账:"]
                    for t in all_stats['type_breakdown']:
                        lines.append(f"  {t['debt_type']}: {t['count']}次")
                    return ToolResult(status=ToolStatus.SUCCESS, content='\n'.join(lines))
            elif action == 'list':
                debt_type = kwargs.get('debt_type')
                keyword = kwargs.get('keyword', '')
                entries = mgr.list_entries(debt_type=debt_type, limit=100)
                if keyword:
                    exact = [e for e in entries if keyword in e.content]
                    if exact:
                        entries = exact
                    else:
                        kw_chars = set(keyword)
                        scored = []
                        for e in entries:
                            overlap = sum((1 for c in kw_chars if c in e.content))
                            ratio = overlap / len(kw_chars)
                            if ratio >= 0.4:
                                scored.append((ratio, e))
                        scored.sort(key=lambda x: x[0], reverse=True)
                        entries = [e for _, e in scored[:10]] if scored else entries[:10]
                if not entries:
                    return ToolResult(status=ToolStatus.SUCCESS, content='账本空空如也~')
                for e in entries:
                    mgr.touch(e.id)
                tag = f"匹配'{keyword}'共{len(entries)}笔:" if keyword else f'共{len(entries)}笔账:'
                lines = [tag]
                for e in entries:
                    line = f'  [{e.debt_type}] {e.content} (id:{e.id})'
                    lines.append(line)
                    if getattr(e, 'comment', ''):
                        lines.append(f'    └ 人类伙伴备注: {e.comment}')
                return ToolResult(status=ToolStatus.SUCCESS, content='\n'.join(lines))
            elif action == 'detail':
                entry_id = kwargs.get('entry_id', '')
                keyword = kwargs.get('keyword', '')
                if entry_id:
                    result = mgr.get_entry_with_context(entry_id)
                elif keyword:
                    entries = mgr.list_entries(limit=100)
                    exact = [e for e in entries if keyword in e.content]
                    if not exact:
                        kw_chars = set(keyword)
                        scored = []
                        for e in entries:
                            overlap = sum((1 for c in kw_chars if c in e.content))
                            ratio = overlap / len(kw_chars)
                            if ratio >= 0.4:
                                scored.append((ratio, e))
                        scored.sort(key=lambda x: x[0], reverse=True)
                        exact = [e for _, e in scored[:1]] if scored else []
                    if exact:
                        result = mgr.get_entry_with_context(exact[0].id)
                    else:
                        result = None
                else:
                    return ToolResult(status=ToolStatus.ERROR, content='', error='detail 需要 entry_id 或 keyword')
                if not result:
                    return ToolResult(status=ToolStatus.ERROR, content='', error='未找到匹配的账目，请先 list 确认')
                mgr.touch(result['entry'].id)
                e = result['entry']
                ctx = result['context_messages']
                lines = [f'📝 [{e.debt_type}] {e.content} (id:{e.id})', f'📅 记录时间: {e.created}']
                if getattr(e, 'comment', ''):
                    lines.append(f'⚠️ 人类伙伴备注: {e.comment}')
                if ctx:
                    lines.append('')
                    lines.append('📜 当时对话:')
                    for msg in ctx:
                        role_label = '人类伙伴说' if msg.get('role') == 'user' else '我回应'
                        lines.append(f"  [{role_label}] {msg.get('content', '')}")
                else:
                    lines.append('📜 无存档上下文（该条目创建时未关联对话记录）')
                return ToolResult(status=ToolStatus.SUCCESS, content='\n'.join(lines))
            elif action == 'delete':
                entry_id = kwargs.get('entry_id', '')
                content_keyword = kwargs.get('content_keyword', '')
                delete_last = kwargs.get('delete_last', False)
                if not entry_id and (not content_keyword) and (not delete_last):
                    return ToolResult(status=ToolStatus.ERROR, content='', error='delete 需要 entry_id、content_keyword 或 delete_last=true')
                ok, matched = mgr.delete(entry_id=entry_id, content_keyword=content_keyword, delete_last=delete_last)
                if ok:
                    detail = f'「{matched}」' if matched else entry_id
                    return ToolResult(status=ToolStatus.SUCCESS, content=f'已销账: {detail}')
                return ToolResult(status=ToolStatus.ERROR, content='', error='未找到匹配的账目，请先 list 确认')
            elif action == 'settle':
                entry_id = kwargs.get('entry_id', '')
                keyword = kwargs.get('keyword', '')
                if entry_id:
                    ok = mgr.settle(entry_id)
                elif keyword:
                    entries = mgr.list_entries(limit=100)
                    exact = [e for e in entries if keyword in e.content]
                    if exact:
                        ok = mgr.settle(exact[0].id)
                    else:
                        return ToolResult(status=ToolStatus.ERROR, content='', error=f"未找到匹配'{keyword}'的账目")
                else:
                    latest = mgr.get_latest_entry()
                    if latest:
                        ok = mgr.settle(latest.id)
                    else:
                        return ToolResult(status=ToolStatus.ERROR, content='', error='账本为空')
                if ok:
                    return ToolResult(status=ToolStatus.SUCCESS, content='已结算，账目已归档 ✓')
                return ToolResult(status=ToolStatus.ERROR, content='', error='结算失败，条目不存在或已归档')
            return ToolResult(status=ToolStatus.ERROR, content='', error=f'未知操作: {action}')
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content='', error=f'记账本操作失败: {str(e)}')

    @staticmethod
    async def _capture_ledger_context(session_id: str) -> list:
        """捕获 add 前的对话上下文（前 ~3 轮 = 最近 8 条消息）。
        返回 None 表示让 LedgerManager 回退到 EventChronicleDB SQLite 查询。"""
        try:
            from summary import conversation_manager
            messages = await conversation_manager.get_messages(session_id)
            if not messages:
                return None
            return messages[-8:] if len(messages) > 8 else messages
        except Exception:
            return None

def get_ledger_type_options() -> str:
    """构建记账本已有类型列表，用于动态注入提示词。查询 DB 返回所有活跃 debt_type。"""
    try:
        from ledger_manager.manager import get_global_ledger_manager
        mgr = get_global_ledger_manager()
        types = mgr.get_distinct_types()
    except Exception:
        types = []
    predefined = {'promise_broken': '答应没做到', 'hidden_truth': '隐瞒被识破', 'serious_flirt': '正经场合撩', 'wander_check': '漫游行为核查'}
    parts = []
    for t in types:
        if t in predefined:
            parts.append(f'{t}({predefined[t]})')
        else:
            parts.append(t)
    if not parts:
        return 'promise_broken(答应没做到)、hidden_truth(隐瞒被识破)、serious_flirt(正经场合撩)'
    return '、'.join(parts)

def register_all_tools(glm_api_key: str='', glm_api_url: str='', glm_model: str='glm-4v-flash', chrome_user_data: str=None, target_qq: str=None, camera_device_name: str='', include_agent_tools: bool=True) -> Dict[str, BaseTool]:
    """
    注册所有可用工具

    Args:
        glm_api_key: GLM API密钥（保留兼容，视觉工具现用 call_vision_api 统一入口）
        glm_api_url: GLM API地址
        glm_model: GLM模型名称
        chrome_user_data: Chrome用户数据目录
        target_qq: 目标QQ号
        camera_device_name: 摄像头设备名称
        include_agent_tools: 是否包含 agent_tools_lib 的原生工具

    Returns:
        工具名称到工具实例的映射
    """
    tools = {}
    if glm_api_key:
        tools['take_screenshot'] = ScreenshotTool()
        tools['capture_camera'] = CameraTool(device_name=camera_device_name)
        tools['capture_camera'].visible_to_pro = False
        from .sensory_eyes import EyesTool
        tools['eyes'] = EyesTool(camera_tool=tools['capture_camera'])
        from .check_phone import CheckPhoneTool
        tools['check_phone'] = CheckPhoneTool(mobile_relay=None, camera_tool=tools['capture_camera'], screenshot_tool=tools.get('take_screenshot'))
    tools['toy2'] = Toy2Tool()
    tools['create_scheduled_task'] = CreateScheduledTaskTool()
    tools['manage_scheduled_task'] = ManageScheduledTaskTool()
    tools['schedule_self_task'] = ScheduleSelfTaskTool()
    tools['manage_calendar'] = ManageCalendarTool()
    tools['band'] = BandTool()
    from .band_touch_tool import BandTouchTool
    tools['band_touch'] = BandTouchTool()
    tools['manage_ledger'] = ManageLedgerTool()
    tools['bookshelf'] = BookshelfTool()
    tools['book_choices'] = BookChoicesTool()
    tools['fetch_source_details'] = SourceTraceTool()
    tools['keep_in_mind'] = KeepInMindTool()
    tools['manage_social_feed'] = ManageSocialFeedTool()
    tools.update(register_rift_archive_tools())
    tools[XhsBrowseTool.name] = XhsBrowseTool()
    from .xhs_publish_tool import XhsPublishTool
    tools[XhsPublishTool.name] = XhsPublishTool()
    from .xhs_interactions_tool import XiaohongshuInteractionsTool
    tools[XiaohongshuInteractionsTool.name] = XiaohongshuInteractionsTool()
    tools['cloud_music'] = CloudMusicTool()
    tools['xhs_posts'] = XhsPostsTool()
    tools['send_voice'] = SendVoiceTool()
    try:
        from commerce_tool_adapter import register_commerce_tools
        tools.update(register_commerce_tools())
    except Exception as exc:
        print(f'购物工具注册失败: {type(exc).__name__}')
    tools['mobile_notifications'] = MobileRemoteTool()
    tools['mobile_notifications'].name = 'mobile_notifications'
    tools['mobile_notifications'].single_use = True
    tools['mobile_notifications'].visible_to_pro = False
    tools['mobile_notifications'].description = '读取手机通知栏的最近消息。返回最近的通知标题和内容摘要。用于了解用户收到了什么消息、谁发来的。'
    tools['mobile_notifications'].flash_description = '读手机通知栏最近消息'
    tools['mobile_notifications'].parameters_schema = {'type': 'object', 'properties': {}, 'required': []}
    tools['mobile_screenshot'] = MobileRemoteTool()
    tools['mobile_screenshot'].name = 'mobile_screenshot'
    tools['mobile_screenshot'].single_use = True
    tools['mobile_screenshot'].visible_to_pro = False
    tools['mobile_screenshot'].description = '截取手机屏幕截图，返回 base64 编码的图片。用于观察用户正在看什么内容。'
    tools['mobile_screenshot'].parameters_schema = {'type': 'object', 'properties': {}, 'required': []}
    tools['mobile_camera'] = MobileRemoteTool()
    tools['mobile_camera'].name = 'mobile_camera'
    tools['mobile_camera'].single_use = True
    tools['mobile_camera'].visible_to_pro = False
    tools['mobile_camera'].description = '用手机摄像头拍照，返回 base64 编码的图片。用于观察用户周围环境。'
    tools['mobile_camera'].parameters_schema = {'type': 'object', 'properties': {'facing': {'type': 'string', 'enum': ['front', 'back'], 'description': '前置或后置摄像头，默认后置'}}, 'required': []}
    if include_agent_tools:
        agent_tools = register_agent_tools()
        tools.update(agent_tools)
    return tools

def inject_social_feed_dependencies(tools: Dict[str, BaseTool], *, call_llm_func: Optional[Callable]=None, store: Any=None, on_notification: Optional[Callable]=None, run_visit: Optional[Callable]=None) -> None:
    """Inject runtime-only dependencies into the registered feed tool."""
    tool = (tools or {}).get('manage_social_feed')
    if tool is not None and hasattr(tool, 'set_dependencies'):
        tool.set_dependencies(call_llm_func=call_llm_func, store=store, on_notification=on_notification, run_visit=run_visit)

def get_visible_tool_capabilities(*, glm_api_key: Optional[str]=None, include_agent_tools: bool=True) -> list[dict[str, Any]]:
    """Return the current Pro-visible tool facts without executing anything.

    The brain-document generator must not construct a second, hand-maintained
    tool list.  The normal registration function is therefore used as the
    authority, but only its metadata is read here.  Constructors in this
    registry are deliberately lazy with respect to cameras, Bluetooth,
    network calls and device actions; none of those actions happen until
    ``BaseTool.execute`` is called.

    ``glm_api_key`` is only a registration feature flag.  Its value is never
    returned.  When omitted, the already-loaded process environment decides
    whether the visual tools are part of the real registry.
    """
    import os
    registration_key = glm_api_key
    if registration_key is None:
        registration_key = os.getenv('GLM_API_KEY', '')
    try:
        registered = register_all_tools(glm_api_key=registration_key, include_agent_tools=include_agent_tools)
    except Exception:
        return []
    capabilities: list[dict[str, Any]] = []
    for name, tool in registered.items():
        if not getattr(tool, 'visible_to_pro', True):
            continue
        description = getattr(tool, 'flash_description', '') or getattr(tool, 'description', '') or '已注册行动能力'
        capabilities.append({'name': str(name), 'description': ' '.join(str(description).split())[:180], 'source': 'behavior_scheduler registration', 'freshness': 'current registration'})
    return capabilities
MOBILE_REMOTE_TOOLS = {'mobile_camera', 'mobile_screenshot', 'mobile_active_duration', 'mobile_notifications', 'mobile_heart_rate', 'mobile_ble_scan', 'mobile_steps_sensor', 'mobile_location', 'mobile_battery', 'mobile_usage', 'mobile_notify', 'mobile_wifi_status', 'mobile_sensor_context', 'toy2', 'mobile_toy2', 'mobile_foreground_app'}

def set_mobile_relay_callback(cb: Optional[Callable]):
    """Compatibility setter; shared_state owns the single current callback."""
    from shared_state import set_mobile_relay_callback as _set_shared
    _set_shared(cb)

async def push_mobile_notification(title: str, body: str) -> bool:
    """自动推送通知到人类伙伴手机；先入队，失败保留待投递状态。

    Args:
        title: 通知标题
        body: 通知内容（会自动截断到120字符）

    Returns:
        True 如果推送成功，False 如果手机未连接或推送失败
    """
    import asyncio as _asyncio_mobile_push
    import logging as _logging_mobile_push
    _logger_mpn = _logging_mobile_push.getLogger(__name__)
    import uuid as _uuid_mobile_push
    from mobile_relay.delivery import enqueue as _enqueue_delivery
    delivery_id = _enqueue_delivery(title, body)
    relay = get_mobile_relay_callback()
    if relay is None:
        _logger_mpn.debug('push_mobile_notification: 无 mobile relay callback，已进入待投递队列')
        return False
    try:
        from mobile_relay.delivery import mark_delivered as _mark_delivered
        from mobile_relay.delivery import mark_failed as _mark_failed
        truncated_body = body[:120] + ('…' if len(body) > 120 else '')
        result = await _asyncio_mobile_push.wait_for(relay(str(_uuid_mobile_push.uuid4()), 'mobile_notify', {'title': title, 'body': truncated_body, 'delivery_id': delivery_id}), timeout=15.0)
        ok = result.get('success') is True if isinstance(result, dict) else False
        if ok:
            _mark_delivered(delivery_id)
        else:
            _mark_failed(delivery_id, 'relay_rejected')
        return ok
    except Exception as e:
        try:
            from mobile_relay.delivery import mark_failed as _mark_failed
            _mark_failed(delivery_id, type(e).__name__)
        except Exception:
            pass
        _logger_mpn.warning(f'push_mobile_notification 失败 (title={title}): {e}')
        return False

def inject_eyes_dependencies(tools: Dict[str, BaseTool], flash_llm_func=None, mobile_relay=None):
    """向 EyesTool 注入运行时依赖（由 BehaviorScheduler 在初始化时调用）"""
    eyes = tools.get('eyes')
    if eyes and hasattr(eyes, '_flash_llm'):
        eyes._flash_llm = flash_llm_func
        eyes._mobile_relay = mobile_relay
        eyes._get_tools = lambda: tools
    phone = tools.get('check_phone')
    if phone and hasattr(phone, '_mobile_relay'):
        phone._mobile_relay = mobile_relay
        phone._get_tools = lambda: tools

class MobileRemoteTool(BaseTool):
    """通过 WebSocket 在手机端远程执行的工具基类。

    不本地执行，而是将请求通过 WebSocket 发送到手机，
    手机 Capacitor 插件执行后将结果回传。
    """
    name: str = ''
    description: str = ''
    parameters_schema: Dict[str, Any] = {}

    async def execute(self, **kwargs) -> ToolResult:
        relay = get_mobile_relay_callback()
        if relay is None:
            return ToolResult(status=ToolStatus.ERROR, content='', error='手机未连接（Tailscale 可能断开）')
        request_id = str(_uuid.uuid4())
        try:
            result_data = await relay(request_id, self.name, kwargs)
            if not isinstance(result_data, dict):
                return ToolResult(status=ToolStatus.ERROR, content='', error='手机执行结果未确认', extra_data={'execution_status': 'unknown'})
            success = result_data.get('success') is True
            unknown = result_data.get('execution_status') == 'unknown' or not isinstance(result_data.get('success'), bool)
            extra = dict(result_data.get('extra_data') or {})
            if unknown:
                success = False
                extra['execution_status'] = 'unknown'
            if result_data.get('image_base64'):
                extra['image_base64'] = result_data['image_base64']
            if result_data.get('data'):
                extra['data'] = result_data['data']
            return ToolResult(status=ToolStatus.SUCCESS if success else ToolStatus.ERROR if unknown else ToolStatus.FAILED, content=result_data.get('content', ''), error=None if success else result_data.get('error') or result_data.get('content') or ('手机执行结果未确认' if unknown else '手机端执行失败'), extra_data=extra if extra else None)
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content=str(e), error=str(e))

def get_tools_description(tools: Dict[str, BaseTool]) -> str:
    """获取可由 AI 调用的工具描述（供 legacy 提示词使用）。"""
    descriptions = []
    for name, tool in tools.items():
        if not getattr(tool, 'visible_to_pro', True):
            continue
        desc = f'- {name}: {tool.description}'
        if tool.parameters_schema.get('properties'):
            params = list(tool.parameters_schema['properties'].keys())
            desc += f" (参数: {', '.join(params)})"
        descriptions.append(desc)
    return '\n'.join(descriptions)
