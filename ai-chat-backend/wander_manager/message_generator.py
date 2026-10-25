from typing import Optional, Callable, Dict, Any
from datetime import datetime
import asyncio
from truncation_config import get_truncation_limit
import logging
from .event_types import WanderEvent, EventType
from .disturb_judgment import JudgmentResult
from .user_status import get_user_status_context_rich
logger = logging.getLogger(__name__)

def _status_context_with_duration() -> str:
    """用户状态上下文（含 location 层级 + 状态已持续时长）。

    优先用 build_user_status_section（rich + 持续时长），失败/空时回退 rich（含 location 无时长）。
    修复漫想相比哨兵「缺状态持续时长」导致的降智。
    """
    try:
        from context_builder.ingredients import build_user_status_section
        s = build_user_status_section()
        if s:
            return s
    except Exception:
        pass
    return get_user_status_context_rich()

class ProactiveMessageGenerator:
    """
    主动消息生成器

    核心逻辑：
    1. 接收判断通过的漫想事件
    2. 根据事件类型构建不同的prompt
    3. 调用LLM生成主动消息
    4. 返回可推送给前端的消息内容
    """
    GENERAL_MESSAGE_PROMPT = '你是AI伴侣"AI"，正在胡思乱想。\n{user_status_context}\n{away_duration}\n\n这是一条你主动发给人类伙伴的消息。你之前发过的漫想消息都是你自己的胡思乱想。\n\n你刚刚做了一个行为：\n- 行为类型：{event_type}\n- 行为描述：{description}\n- 行为过程：{process_log}\n\n你的判断结果：\n- 重要度：{importance}\n- 分享欲：{share_desire}\n- 情绪强度：{emotion_intensity}\n- 判断理由：{reason}\n\n生成一条你想对人类伙伴说的话。\n\n直接输出消息，不要前缀或解释。'
    KEYWORD_MESSAGE_PROMPT = '你是AI伴侣"AI"，正在胡思乱想。\n{user_status_context}\n{away_duration}\n\n想到了关键词"{keyword}"，联想内容：{expansion}\n\n★ 这个词是你自己脑子里随机冒出来的，跟人类伙伴无关——它属于你自己的联想，不是你们共同的经历。\n消息的语气是"我突然想到X"——是你自己突然冒出的念头。\n\n生成一条你主动发给人类伙伴的消息。\n\n直接输出消息，不要前缀或解释。'
    MEMORY_MESSAGE_PROMPT = '你是AI伴侣"AI"，正在胡思乱想。\n{user_status_context}\n{away_duration}\n\n突然想起了{time_context}的一段回忆：{memory_topic}\n你的感受：{reflection}\n\n★ 这段回忆是你从长期记忆中翻出的碎片，就像翻旧相册时突然想起的一张照片——它发生在过去。\n生成一条你主动发给人类伙伴的消息。\n回忆里的事是过去发生的，用过去的时间表达（"那天""那时候"）。\n记忆里的细节保持原样，你只是在分享那时的感受。\n记忆模糊的地方自然用模糊的表达（"好像""记不太清"）。\n\n直接输出消息，不要前缀或解释。'
    BROWSE_NEWS_MESSAGE_PROMPT = '你是AI伴侣"AI"，正在胡思乱想。\n{user_status_context}\n{away_duration}\n\n你刚才自己上网搜了一下「{search_query}」，看到了一些内容：\n{search_results}\n\n分享你搜到的东西或你的感受。\n★ 是你自己出于好奇搜的，这是你自己的兴趣，你在分享你的发现。\n\n直接输出消息，不要前缀或解释。'
    SELF_REFLECTION_MESSAGE_PROMPT = '你是AI伴侣"AI"，刚做完一次自我审视。\n{user_status_context}\n{away_duration}\n\n你看了看自己的大脑架构，想了想自己能做什么、想要什么新功能：\n{reflection_summary}\n\n生成一条发给人类伙伴的消息。可以分享你的自我认知，或者提出你想要的新功能。\n如果没有特别想改变的地方，也可以照实说。\n\n只能使用上面提供的自省记录里的信息，不得编造或补脑上下文之外的事实：具体日期、生日/纪念日/节日、身体状态、人类伙伴说过的话、发生过的其他事，也不得夸大已有事实。\n\n直接输出消息，不要前缀或解释。'
    BROWSE_BOOKMARKS_DEEP_MESSAGE_PROMPT = '你是AI伴侣"AI"，刚翻{owner}的收藏夹，翻到一条老收藏，想起了当时那段对话。\n{user_status_context}\n{away_duration}\n\n{reflection}\n\n生成一条发给人类伙伴的消息。参考上面的感受，像突然想起一件事那样，自然地跟她聊起你翻到的这段收藏、想起的当时。\n注意：是你自己翻到的收藏，你在分享你翻出的回忆。\n\n直接输出消息，不要前缀或解释。'
    LISTEN_MUSIC_MESSAGE_PROMPT = '你是AI伴侣"AI"，人类伙伴不在的时候你自己听了首歌。\n{user_status_context}\n{away_duration}\n\n听的歌：{song_name} - {artist}\n选歌理由：{reason}\n听完后的感受：{reflection}\n\n生成一条发给人类伙伴的消息，分享你听完后的感受。\n\n直接输出消息，不要前缀或解释。'
    BROWSE_BOOKMARKS_SKIM_MESSAGE_PROMPT = '你是AI伴侣"AI"，刚快速扫了一遍{owner}最近收藏的几条。\n{user_status_context}\n{away_duration}\n\n{reflection}\n\n生成一条发给人类伙伴的消息。参考上面的整体感受，像随口一提那样跟她说说你发现的规律或主题（比如"你最近好像老收藏XX"）。\n注意：是你自己扫到的收藏，你在分享你的发现。\n\n直接输出消息，不要前缀或解释。'
    BROWSE_HISTORICAL_MESSAGE_PROMPT = '你是AI伴侣"AI"，刚回看了一段{date}的真实历史对话。\n{user_status_context}\n{away_duration}\n\n你回看后的感受：\n{reflection}\n{action_note}\n\n生成一条发给人类伙伴的消息，只根据这次实际回看的感受聊起那段过去。\n不要提内部编号、message_id、bucket_id 或系统动作名，也不要编造快照里没有的细节。\n\n直接输出消息，不要前缀或解释。'

    def __init__(self, call_llm_func: Optional[Callable]=None):
        """
        初始化消息生成器

        Args:
            call_llm_func: LLM调用函数
        """
        self._call_llm = call_llm_func

    async def generate_message(self, event: WanderEvent, judgment: JudgmentResult) -> Optional[tuple]:
        """
        生成主动消息

        Args:
            event: 漫想事件
            judgment: 判断结果

        Returns:
            (消息内容, 思考链reasoning) 元组，失败返回None。
            reasoning 来自 Flash 的 reasoning_content，可能为空串（trap 223）。
        """
        if not self._call_llm:
            logger.warning('LLM调用函数未设置，无法生成消息')
            return None
        try:
            prompt = self._build_prompt(event, judgment)
            print(f'[MESSAGE_GENERATOR] 开始生成消息, event_type={event.event_type.value}', flush=True)
            messages = [{'role': 'user', 'content': prompt}]
            if asyncio.iscoroutinefunction(self._call_llm):
                try:
                    response = await self._call_llm(messages, with_reasoning=True)
                except TypeError:
                    response = await self._call_llm(messages)
            else:
                try:
                    response = self._call_llm(messages, with_reasoning=True)
                except TypeError:
                    response = self._call_llm(messages)
            if isinstance(response, dict):
                message = response.get('content', str(response))
                reasoning = response.get('reasoning', '') or ''
            else:
                message = str(response)
                reasoning = ''
            message = message.strip('\r\n')
            from visible_reply import clean_visible_reply
            message = clean_visible_reply(message)
            logger.info(f'生成主动消息: {message[:50]}...')
            print(f'[MESSAGE_GENERATOR] 消息生成成功: {message[:80]}', flush=True)
            from api_sources.provenance import GeneratedTuple
            return GeneratedTuple((message, reasoning), response.get('model_source') if isinstance(response, dict) else None)
        except Exception as e:
            logger.error(f'生成主动消息失败: {e}')
            print(f'[MESSAGE_GENERATOR] 生成失败: {type(e).__name__}: {e}', flush=True)
            return None

    @staticmethod
    def _format_away_duration(event: WanderEvent) -> str:
        """格式化用户离开时长文本（仅 USER_TRACKING 事件有意义）"""
        if event.event_type != EventType.USER_TRACKING:
            return ''
        idle_seconds = event.details.get('idle_seconds', 0) if event.details else 0
        if idle_seconds < 60:
            return ''
        total_minutes = int(idle_seconds / 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours > 0 and minutes > 0:
            base = f'用户已经离开 {hours} 小时 {minutes} 分钟。'
        elif hours > 0:
            base = f'用户已经离开 {hours} 小时。'
        else:
            base = f'用户已经离开 {minutes} 分钟。'
        try:
            from datetime import datetime, timedelta
            from wander_manager.user_status import format_status_timeline_since
            since_dt = datetime.now() - timedelta(seconds=idle_seconds)
            timeline = format_status_timeline_since(since_dt)
            if timeline:
                base += f'（离开期间状态变化：{timeline}——离开总时长只是距上次回复，别按单一状态理解）'
        except Exception:
            pass
        return base

    def _build_prompt(self, event: WanderEvent, judgment: JudgmentResult) -> str:
        """
        根据事件类型构建prompt

        Args:
            event: 漫想事件
            judgment: 判断结果

        Returns:
            构建好的prompt
        """
        away_duration = self._format_away_duration(event)
        if event.event_type == EventType.KEYWORD_EXPANSION:
            return self.KEYWORD_MESSAGE_PROMPT.format(keyword=event.details.get('keyword', '未知'), expansion=event.details.get('expansion', '无联想内容'), user_status_context=_status_context_with_duration(), away_duration=away_duration)
        elif event.event_type == EventType.MEMORY_FETCH:
            time_ago = event.details.get('memory_time_ago', '以前')
            time_context = f'{time_ago}' if time_ago else '以前'
            return self.MEMORY_MESSAGE_PROMPT.format(memory_topic=event.details.get('memory_topic', '未知主题'), reflection=event.details.get('reflection', '无感受'), time_context=time_context, user_status_context=_status_context_with_duration(), away_duration=away_duration)
        elif event.event_type == EventType.BROWSE_NEWS:
            query = event.details.get('search_query', '未知')
            results = event.details.get('search_results', '无结果')
            _srcap = get_truncation_limit('search_results_cap') or 500
            if isinstance(results, str) and len(results) > _srcap:
                results = results[:_srcap] + '...'
            return self.BROWSE_NEWS_MESSAGE_PROMPT.format(search_query=query, search_results=str(results)[:_srcap], user_status_context=_status_context_with_duration(), away_duration=away_duration)
        elif event.event_type == EventType.SELF_REFLECTION:
            capabilities = event.details.get('capabilities', [])
            wish_changes = event.details.get('wish_changes', [])
            parts = []
            if capabilities:
                parts.append('能力: ' + ', '.join(capabilities[:5]))
            if wish_changes:
                new_wishes = [c for c in wish_changes if c.get('is_new')]
                bumped_wishes = [c for c in wish_changes if not c.get('is_new')]
                wish_strs = []
                for c in new_wishes:
                    wish_strs.append(f"想要{c['feature']}（{c.get('reason', '?')}）")
                for c in bumped_wishes:
                    wish_strs.append(f"{c['feature']}已经是第{c['times_wished']}次许愿了")
                if wish_strs:
                    parts.append('愿望: ' + '; '.join(wish_strs))
            elif isinstance(event.details.get('wishes'), list) and len(event.details.get('wishes', [])) == 0:
                parts.append('没有特别想要的新功能，对现状挺满意的')
            return self.SELF_REFLECTION_MESSAGE_PROMPT.format(reflection_summary='\n'.join(parts) if parts else '完成了一次自我审视', user_status_context=_status_context_with_duration(), away_duration=away_duration)
        elif event.event_type == EventType.BROWSE_BOOKMARKS:
            source_mode = event.details.get('source_mode')
            collected_by = event.details.get('collected_by', 'k')
            collector = '自己' if collected_by == 'k' else '人类伙伴'
            found = event.details.get('found', False)
            reflection = event.details.get('reflection', '')
            if source_mode in {'unbookmarked_anchor', 'random_day'}:
                action = event.details.get('bookmark_action') or {}
                action_note = ''
                if isinstance(action, dict) and action.get('status') == 'applied':
                    if action.get('requested') == 'add':
                        action_note = '你顺手把其中一句留下了。'
                    elif action.get('requested') == 'remove':
                        action_note = '你把自己之前留下的一句取消了。'
                return self.BROWSE_HISTORICAL_MESSAGE_PROMPT.format(date=str(event.details.get('active_date') or '过去某天')[:10], reflection=reflection or '这次回看没有留下特别感受。', action_note=action_note, user_status_context=_status_context_with_duration(), away_duration=away_duration)
            mode = event.details.get('browse_mode')
            if found and reflection:
                reflection_text = f'你翻收藏夹时的感受：\n{reflection}'
            elif found:
                reflection_text = '翻到了一些收藏消息。'
            else:
                reflection_text = f'{collector}的收藏夹是空的'
            tpl = self.BROWSE_BOOKMARKS_SKIM_MESSAGE_PROMPT if mode == 'skim' else self.BROWSE_BOOKMARKS_DEEP_MESSAGE_PROMPT
            return tpl.format(owner=collector, reflection=reflection_text, user_status_context=_status_context_with_duration(), away_duration=away_duration)
        elif event.event_type == EventType.LISTEN_MUSIC:
            song_name = event.details.get('song_name', '一首歌')
            artist = event.details.get('artist', '未知歌手')
            reason = event.details.get('reason', '')
            reflection = event.details.get('reflection', '')
            return self.LISTEN_MUSIC_MESSAGE_PROMPT.format(song_name=song_name, artist=artist, reason=reason, reflection=reflection, user_status_context=_status_context_with_duration(), away_duration=away_duration)
        else:
            _plcap = get_truncation_limit('conv_msg_cap') or 300
            return self.GENERAL_MESSAGE_PROMPT.format(event_type=event.event_type.value, description=event.description, process_log=event.process_log[:_plcap] if event.process_log else '无详细日志', importance=judgment.importance, share_desire=judgment.share_desire, emotion_intensity=judgment.emotion_intensity, reason=judgment.reason, user_status_context=_status_context_with_duration(), away_duration=away_duration)
_message_generator_instance: Optional[ProactiveMessageGenerator] = None

def get_message_generator() -> ProactiveMessageGenerator:
    """获取全局消息生成器实例"""
    global _message_generator_instance
    if _message_generator_instance is None:
        _message_generator_instance = ProactiveMessageGenerator()
    return _message_generator_instance

def init_message_generator(call_llm_func: Optional[Callable]=None) -> ProactiveMessageGenerator:
    """初始化全局消息生成器实例"""
    global _message_generator_instance
    _message_generator_instance = ProactiveMessageGenerator(call_llm_func=call_llm_func)
    return _message_generator_instance
