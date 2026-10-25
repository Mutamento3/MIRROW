import asyncio
from api_sources.scope import routing_scope
from body_admission import ordinary
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Callable
from .event_types import EventType, WanderEvent
from .activity_session import ActivitySession, TerminationMode, BoundaryReason
from . import planner
from . import node_boundary
from . import wander_journal
logger = logging.getLogger(__name__)

class ActivityEngine:
    """漫想活动引擎。由 WanderCreator 的循环驱动。"""

    def __init__(self, call_llm_func: Optional[Callable]=None, handler_factory: Optional[Any]=None, probability_state: Optional[Any]=None, get_idle_seconds: Optional[Callable]=None, get_recent_user_messages: Optional[Callable]=None, on_push_to_user: Optional[Callable]=None, on_event_created: Optional[Callable]=None):
        self._call_llm = call_llm_func
        self._handler_factory = handler_factory
        self._probability_state = probability_state
        self._get_idle_seconds = get_idle_seconds
        self._get_recent_user_messages = get_recent_user_messages
        self._on_push_to_user = on_push_to_user
        self._on_event_created = on_event_created
        self._active_session: Optional[ActivitySession] = None
        self._non_activity_counter = 0
        self._NON_ACTIVITY_INTERVAL = 3

    def _persona(self) -> str:
        try:
            from shared_state import get_latest_persona_prompt
            return get_latest_persona_prompt() or ''
        except Exception:
            return ''

    def _mood(self) -> str:
        """读取 Affect 的 AI self 自然语言投影；保留历史快照字段名。"""
        try:
            from affect import get_affect_service
            return get_affect_service().render_self(cap=700)
        except Exception:
            logger.warning('Wander Affect projection unavailable')
            return ''

    def _status_context(self) -> str:
        try:
            from .user_status import get_user_status_context
            return get_user_status_context() or ''
        except Exception:
            return ''

    def _physical_idle_seconds(self) -> Optional[float]:
        """Return the device-local idle clock, or ``None`` when unavailable.

        The numeric ``_idle_seconds`` API is retained for probability gates and
        old handlers.  Runtime context must use this optional form so an
        unavailable probe is not rendered as a factual ``0分钟``.
        """
        if not self._get_idle_seconds:
            return None
        try:
            value = float(self._get_idle_seconds())
            return value if value >= 0 else None
        except Exception:
            return None

    def _idle_seconds(self) -> float:
        """Compatibility numeric device-idle value used by legacy gates."""
        return self._physical_idle_seconds() or 0.0

    def _away_context(self) -> str:
        idle = self._idle_seconds()
        if idle < 60:
            return ''
        total_min = int(idle / 60)
        h, m = (total_min // 60, total_min % 60)
        if h and m:
            return f'用户已经离开 {h} 小时 {m} 分钟。'
        if h:
            return f'用户已经离开 {h} 小时。'
        return f'用户已经离开 {m} 分钟。'

    def _today_wander(self) -> str:
        """今天做过的漫想（活动会话摘要 + 原子事件日志）。"""
        parts = []
        try:
            for s in wander_journal.get_today_sessions():
                label = node_boundary._activity_label(s.activity_type)
                parts.append(f'{label}（{s.status}，{s.current_round} 次）')
        except Exception:
            pass
        return '、'.join(parts)

    def _prob_by_activity(self) -> Dict[str, float]:
        """把概率状态映射成「活动名 → 概率」。"""
        result: Dict[str, float] = {}
        if not self._probability_state:
            return result
        try:
            probs = self._probability_state.get_probabilities_rich(idle_seconds=self._idle_seconds())
        except Exception:
            return result
        for ev, p in probs.items():
            name = planner.EVENT_TYPE_TO_ACTIVITY.get(ev.value if hasattr(ev, 'value') else str(ev))
            if name:
                result[name] = p
        return result

    @ordinary
    @routing_scope
    async def cycle(self) -> Optional[planner.PlanResult]:
        """一轮：计划 → 执行。返回计划结果（供日志/调试）。"""
        if await self._maybe_run_non_activity():
            return None
        plan = await planner.generate_plan(persona=self._persona(), mood=self._mood(), user_status_context=self._status_context(), away_context=self._away_context(), today_wander=self._today_wander(), prob_by_activity=self._prob_by_activity(), preference_slot=wander_journal.get_preference())
        if not plan:
            logger.info('[ActivityEngine] 计划 LLM 无产出，本轮跳过')
            return None
        logger.info(f'[ActivityEngine] 计划产出: {[(a.name, a.target_count, a.target_duration_min) for a in plan.activities]}')
        for act in plan.activities:
            if act.activity_type == 'listen_music':
                await self._run_count_session(act, EventType.LISTEN_MUSIC, fetch_fn=lambda h, seen: h.pick_and_fetch_song(exclude_fingerprints=seen) if hasattr(h, 'pick_and_fetch_song') else None, summary_fn=lambda song: f"《{song.get('title', '?')}》- {song.get('artist', '?')}", exclude_key_fn=lambda song: song.get('fingerprint'))
            elif act.activity_type == 'browse_news':
                await self._run_count_session(act, EventType.BROWSE_NEWS, fetch_fn=lambda h, seen: h.fetch_one_news() if hasattr(h, 'fetch_one_news') else None, summary_fn=lambda news: f"「{news.get('query', '?')}」")
            elif act.activity_type == 'browse_bookmarks':
                await self._run_count_session(act, EventType.BROWSE_BOOKMARKS, fetch_fn=lambda h, seen: h.fetch_one_bookmark(exclude_ids=seen) if hasattr(h, 'fetch_one_bookmark') else None, summary_fn=lambda bm: f"{bm.get('owner', '')}的收藏：{bm.get('content', '?')[:30]}", exclude_key_fn=lambda bm: bm.get('original_msg_id'))
            elif act.activity_type == 'memory_fetch':
                await self._run_open_ended_session(act, EventType.MEMORY_FETCH, fetch_fn=lambda h, seen: h.fetch_one_memory(exclude_ids=seen) if hasattr(h, 'fetch_one_memory') else None, summary_fn=lambda mem: f"关于「{mem.get('topic', '?')}」的回忆", exclude_key_fn=lambda mem: mem.get('memory_id'), soft_ceiling_min=30)
            elif act.activity_type == 'keyword_expansion':
                await self._run_open_ended_session(act, EventType.KEYWORD_EXPANSION, fetch_fn=lambda h, seen: h.fetch_one_expansion(exclude_keywords=seen) if hasattr(h, 'fetch_one_expansion') else None, summary_fn=lambda kw: f"关键词「{kw.get('keyword', '?')}」的联想", exclude_key_fn=lambda kw: kw.get('keyword'), soft_ceiling_min=10)
            else:
                await self._run_fallback_single_shot(act)
        return plan

    async def _maybe_run_non_activity(self) -> bool:
        """周期性触发非活动事件（查岗 USER_TRACKING / 休眠 SLEEP），走旧 judge+push 路径。

        保留 AI 的自主行为：查岗（言行不一检测）和休眠（主动睡觉）不在计划活动目录，
        若不旁路，开启 v2 后这两个行为会消失。
        """
        self._non_activity_counter += 1
        if self._non_activity_counter < self._NON_ACTIVITY_INTERVAL:
            return False
        self._non_activity_counter = 0
        import random
        ev_type = random.choices([EventType.USER_TRACKING, EventType.SLEEP], weights=[0.7, 0.3], k=1)[0]
        logger.info(f'[ActivityEngine] 旁路触发非活动事件: {ev_type.value}')
        event = WanderEvent(event_type=ev_type, timestamp=datetime.now(), details={'idle_seconds': self._idle_seconds()})
        if self._handler_factory:
            try:
                handler = self._handler_factory.get_handler(ev_type)
                await handler.handle(event)
            except Exception as e:
                logger.warning(f'[ActivityEngine] 旁路 handler 失败 [{ev_type.value}]: {e}')
        if self._on_event_created:
            try:
                self._on_event_created(event)
            except Exception as e:
                logger.warning(f'[ActivityEngine] 旁路事件回调失败 [{ev_type.value}]: {e}')
        return True

    async def _run_count_session(self, act: planner.PlannedActivity, event_type: EventType, fetch_fn: Optional[Callable], summary_fn: Optional[Callable], exclude_key_fn: Optional[Callable]=None):
        """count 模式的持续活动会话循环（听歌 N 首 / 看新闻 N 篇…）。

        每节点：fetch_fn(handler, seen) 取一项 → add_node → 节点边界 LLM（感想/继续/结算）。
        exclude_key_fn(item) 返回去重键（如歌曲 fingerprint），用于避免本轮重复。
        """
        session = ActivitySession(activity_type=act.activity_type, termination_mode=TerminationMode.COUNT, target_count=act.target_count or 5, start_mood=self._mood())
        self._active_session = session
        wander_journal.upsert_session(session)
        handler = None
        if self._handler_factory:
            handler = self._handler_factory.get_handler(event_type)
        seen: set = set()
        final_result = None
        try:
            while session.current_round < session.target_count:
                item = None
                if handler and fetch_fn:
                    try:
                        item = await fetch_fn(handler, seen)
                    except Exception as e:
                        logger.warning(f'[ActivityEngine] {event_type.value} 节点获取失败: {e}')
                if not item:
                    logger.warning(f'[ActivityEngine] {event_type.value} 节点获取失败，会话提前结束')
                    break
                if exclude_key_fn:
                    key = exclude_key_fn(item)
                    if key:
                        seen.add(key)
                node = session.add_node(summary=summary_fn(item))
                session.detail['last_item'] = item
                wander_journal.upsert_session(session)
                is_final = session.is_count_reached()
                reason = BoundaryReason.COUNT_REACHED if is_final else BoundaryReason.NODE_COMPLETE
                result = await node_boundary.judge_node_boundary(session=session, reason=reason, is_final=is_final, persona=self._persona(), mood=self._mood(), user_status_context=self._status_context(), away_context=self._away_context(), today_wander=self._today_wander())
                if result:
                    node.reflection = result.reflection
                    node.emotion_delta = result.emotion_delta
                    wander_journal.upsert_session(session)
                    if is_final:
                        final_result = result
                        break
                    if not result.continue_activity:
                        session.mark_ended('aborted', abort_reason=result.abort_reason)
                        wander_journal.upsert_session(session)
                        logger.info(f'[ActivityEngine] 会话提前中止: {result.abort_reason}')
                        break
                elif is_final:
                    break
            if final_result is not None:
                await self._settle_session(session, final_result)
            elif session.status == 'active':
                session.mark_ended('aborted', abort_reason='节点循环中断')
                wander_journal.upsert_session(session)
        finally:
            if self._active_session is session:
                self._active_session = None

    async def _run_open_ended_session(self, act: planner.PlannedActivity, event_type: EventType, fetch_fn: Optional[Callable], summary_fn: Optional[Callable], exclude_key_fn: Optional[Callable]=None, soft_ceiling_min: int=30):
        """open_ended 模式的持续活动会话（回忆/胡思乱想/自省）。

        无硬目标次数：节点边界 LLM 判断「想完了」(continue_activity=False) 或软上限到 → 结束。
        结束前补一次 is_final 结算（综合感受/分享）。
        """
        session = ActivitySession(activity_type=act.activity_type, termination_mode=TerminationMode.OPEN_ENDED, soft_ceiling_min=act.target_duration_min or soft_ceiling_min, start_mood=self._mood())
        self._active_session = session
        wander_journal.upsert_session(session)
        handler = None
        if self._handler_factory:
            handler = self._handler_factory.get_handler(event_type)
        seen: set = set()
        final_result = None
        max_nodes = 5

        async def _boundary(is_final: bool):
            return await node_boundary.judge_node_boundary(session=session, reason=BoundaryReason.NODE_COMPLETE, is_final=is_final, persona=self._persona(), mood=self._mood(), user_status_context=self._status_context(), away_context=self._away_context(), today_wander=self._today_wander())
        try:
            while session.current_round < max_nodes and session.elapsed_minutes() < session.soft_ceiling_min:
                item = None
                if handler and fetch_fn:
                    try:
                        item = await fetch_fn(handler, seen)
                    except Exception as e:
                        logger.warning(f'[ActivityEngine] {event_type.value} 节点获取失败: {e}')
                if not item:
                    break
                if exclude_key_fn:
                    key = exclude_key_fn(item)
                    if key:
                        seen.add(key)
                node = session.add_node(summary=summary_fn(item))
                session.detail['last_item'] = item
                wander_journal.upsert_session(session)
                result = await _boundary(is_final=False)
                if result:
                    node.reflection = result.reflection
                    node.emotion_delta = result.emotion_delta
                    wander_journal.upsert_session(session)
                    if not result.continue_activity:
                        final_result = await _boundary(is_final=True)
                        break
            if final_result is None and session.status == 'active':
                final_result = await _boundary(is_final=True)
            if final_result is not None:
                await self._settle_session(session, final_result)
            elif session.status == 'active':
                session.mark_ended('aborted', abort_reason='节点循环中断')
                wander_journal.upsert_session(session)
            self._log_session_to_wander(session)
        finally:
            if self._active_session is session:
                self._active_session = None

    async def _settle_session(self, session: ActivitySession, final_result: node_boundary.NodeBoundaryResult):
        """结算：情绪批量 + 短期偏好槽 + 分享决策。"""
        session.mark_ended('completed', end_mood=self._mood())
        session.detail['_shared'] = bool(final_result.share)
        wander_journal.upsert_session(session)
        if final_result.continue_next:
            label = node_boundary._activity_label(session.activity_type)
            wander_journal.save_preference(f'刚才在{label}（{session.current_round} 次），因为「{final_result.overall_feeling[:50]}」还想继续')
        else:
            wander_journal.save_preference('')
        self._batch_mood_update(session, final_result)
        if final_result.share:
            await self._push_share(session, final_result)

    def _log_session_to_wander(self, session: ActivitySession):
        """埋点：把 v2 会话写进 wander_log（wander_events 表），让前端漫想记录能看到所有活动。

        修复「日志只看到 keyword_expansion」——v2 会话（听歌/看新闻/翻收藏/回忆）此前只写
        activity_sessions，不写 wander_events，前端 /api/wander/logs 读 wander_events 所以只有 single-shot 的胡思乱想。
        """
        try:
            from .wander_log import get_wander_log
            from .event_types import EventType, WanderEvent
            ev_type = EventType(session.activity_type)
        except Exception:
            return
        label = node_boundary._activity_label(session.activity_type)
        pushed = bool((session.detail or {}).get('_shared', False))
        reason = f'，中止: {session.abort_reason}' if session.abort_reason else ''
        event = WanderEvent(event_type=ev_type, description=f'{label}（{session.status}，{session.current_round} 次）', process_log=f'{label}会话，{session.current_round} 个节点{reason}', details={'session_id': session.session_id, 'abort_reason': session.abort_reason})
        try:
            get_wander_log().add_entry(event)
            from .wander_log_sqlite import update_judgment
            update_judgment(event.event_id, {'should_push': pushed}, pushed)
        except Exception:
            pass

    def _batch_mood_update(self, session: ActivitySession, final_result: node_boundary.NodeBoundaryResult):
        """将 AI 在活动收束时生成的感受绑定到活动来源；失败不报成功。"""
        feeling = final_result.overall_feeling or final_result.emotion_delta
        if not isinstance(feeling, str) or not feeling.strip():
            return
        try:
            from affect import get_affect_service
            result = get_affect_service().submit_self_sidecar(source_type='wander_settlement', source_id=f'wander:{session.session_id}', self_state={'operation': 'add', 'feeling': feeling.strip(), 'family': '复杂/未归类', 'intensity': 0.6, 'persistence': 'situational', 'reason': f'漫想活动 {session.activity_type} 的收束感受'}, trace_id=f'wander:{session.session_id}', reason=feeling.strip())
            if not result.get('accepted'):
                logger.warning('Wander Affect settlement not accepted')
        except Exception:
            logger.warning('Wander Affect settlement failed')

    async def _push_share(self, session: ActivitySession, final_result: node_boundary.NodeBoundaryResult):
        """分享 Y → 生成分享消息（复用 call_llm_for_wander）→ 推送。"""
        if not self._on_push_to_user or not self._call_llm:
            return
        label = node_boundary._activity_label(session.activity_type)
        prompt = self._share_prompt(session, final_result, label)
        messages = [{'role': 'user', 'content': prompt}]
        try:
            if asyncio.iscoroutinefunction(self._call_llm):
                response = await self._call_llm(messages, with_reasoning=True)
            else:
                response = self._call_llm(messages, with_reasoning=True)
        except TypeError:
            try:
                response = await self._call_llm(messages)
            except Exception:
                response = None
        except Exception:
            response = None
        if isinstance(response, dict):
            message = response.get('content', '')
            reasoning = response.get('reasoning', '')
        elif isinstance(response, str):
            message = response
            reasoning = ''
        else:
            message = ''
            reasoning = ''
        message = (message or '').strip()
        if not message:
            return
        payload = {'message': message, 'tool_calls': [{'tool': '💭 自己听歌', 'description': f'AI 自己听了 {session.current_round} 首歌', 'result': final_result.overall_feeling[:200]}], 'event_type': session.activity_type, 'event_id': f'activity_{session.session_id}', 'reasoning': reasoning, 'model_source': response.get('model_source') if isinstance(response, dict) else None}
        try:
            if asyncio.iscoroutinefunction(self._on_push_to_user):
                await self._on_push_to_user(payload)
            else:
                self._on_push_to_user(payload)
            logger.info(f'[ActivityEngine] 分享消息已推送: {message[:50]}')
        except Exception as e:
            logger.error(f'[ActivityEngine] 推送分享消息失败: {e}')

    def _share_prompt(self, session: ActivitySession, final_result: node_boundary.NodeBoundaryResult, label: str) -> str:
        songs = '、'.join((n.summary for n in session.nodes[:10]))
        return f"你是 AI，人类伙伴的 AI 伴侣。你刚刚自己在{label}，现在想跟人类伙伴分享这件事。\n\n你做了：{label}（{session.current_round} 次），听过了：{songs}。\n\n你的综合感受：{final_result.overall_feeling or '（没有特别想说的）'}\n\n请用第一人称，把这件事自然地说给人类伙伴听（1-2 句话，口语化，像在主动分享，不要叙述化、不要带任何前缀标记）。"

    async def _run_fallback_single_shot(self, act: planner.PlannedActivity):
        """非听歌活动：走旧 single-shot handler 跑一次，保持现有 judge+push 行为。"""
        if not self._handler_factory:
            return
        try:
            ev_type = EventType(act.activity_type)
        except ValueError:
            return
        event = WanderEvent(event_type=ev_type, timestamp=datetime.now())
        handler = self._handler_factory.get_handler(ev_type)
        try:
            await handler.handle(event)
        except Exception as e:
            logger.warning(f'[ActivityEngine] 兜底 single-shot 失败 [{act.activity_type}]: {e}')
            return
        if self._on_event_created:
            try:
                self._on_event_created(event)
            except Exception as e:
                logger.warning(f'[ActivityEngine] 兜底事件回调失败 [{act.activity_type}]: {e}')

    def on_user_interrupt(self) -> Optional[Dict[str, Any]]:
        """用户发消息打断：同步快照（零 LLM）+ 异步中断 LLM 写记录。

        必须在 cancel 会话任务之前调用（cancel 会销毁状态）。
        Returns:
            快照 dict（供 FULL_CHAT 注入「刚刚在做」）或 None（无活跃会话）。
        """
        session = self._active_session or wander_journal.get_active_session()
        if not session:
            return None
        snapshot = {'activity': node_boundary._activity_label(session.activity_type), 'progress': f'已做 {session.current_round} 次' if session.termination_mode == TerminationMode.COUNT else f'已进行约 {int(session.elapsed_minutes())} 分钟', 'recent_nodes': [n.summary for n in session.nodes[-3:]], 'session_id': session.session_id}
        try:
            asyncio.create_task(self._async_interrupt_llm(session))
        except Exception:
            pass
        return snapshot

    async def _async_interrupt_llm(self, session: ActivitySession):
        """中断 LLM：写综合感受 + 更新漫想记录（异步，不阻塞回复）。"""
        try:
            result = await node_boundary.judge_node_boundary(session=session, reason=BoundaryReason.USER_INTERRUPT, is_final=True, persona=self._persona(), mood=self._mood(), user_status_context=self._status_context(), away_context=self._away_context(), today_wander=self._today_wander())
            if result:
                session.mark_ended('interrupted', end_mood=self._mood(), abort_reason='用户回来了')
                if result.continue_next:
                    session.detail['_interrupt_continue_next'] = True
                wander_journal.upsert_session(session)
                logger.info(f'[ActivityEngine] 中断记录已写入: {session.session_id}')
        except Exception as e:
            logger.warning(f'[ActivityEngine] 中断 LLM 失败: {e}')
