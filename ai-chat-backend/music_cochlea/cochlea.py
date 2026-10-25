"""
音乐耳蜗主循环 — MusicCochlea

生命周期：start() → 后台循环 → stop()
参考 phone_browser.py 的单例 + 回调注入模式

管道：SongDetector检测 → enricher丰富 → Flash判断 → Flash生成 → 推送
"""

import asyncio
from body_admission import optional_observation
import time
import logging
from typing import Optional, Callable, Any

from shared_state import get_music_mode_active, get_last_user_message_time
from .models import SongIdentity, SongMetadata, MusicCochleaState
from .cache import SongCache, REACTION_REFRESH_DAYS

logger = logging.getLogger(__name__)

# ── 常量 ──
TICK_INTERVAL = 1.0                # 主循环间隔（秒）
PUSH_DEFER_SECONDS = 120           # 聊天中抑制推送窗口
COOLDOWN_PER_SONG = 3 * 60         # 同一首歌冷却（秒）
MIN_PLAY_SECONDS = 10              # 秒切过滤：播放 <10s 不推（用户不想听）
IDLE_WARNING_MINUTES = 10          # 无音乐提醒（默认，settings 可覆盖）
NODE2_THRESHOLD = 45               # 节点 2：播放中段推送阈值（秒）

# ── 单例 ──
_cochlea: Optional["MusicCochlea"] = None


def get_cochlea() -> Optional["MusicCochlea"]:
    return _cochlea


def setup_cochlea(
    push_callback: Callable = None,
    flash_llm_func: Callable = None,
    message_llm_func: Callable = None,
) -> "MusicCochlea":
    global _cochlea
    _cochlea = MusicCochlea(
        push_callback=push_callback,
        flash_llm_func=flash_llm_func,
        message_llm_func=message_llm_func,
    )
    return _cochlea


class MusicCochlea:
    """音乐耳蜗主循环"""

    def __init__(self, push_callback=None, flash_llm_func=None, message_llm_func=None):
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 外部注入
        self._push_callback = push_callback      # async fn(msg_content, is_music_cochlea=True)
        self._flash_llm_func = flash_llm_func     # async fn(prompt) → str
        self._message_llm_func = message_llm_func # async fn(messages) → response dict

        # 组件
        from .capture import SongDetector
        self._detector = SongDetector(
            on_song_change=self._on_new_song,
            on_song_end=self._on_song_ended,
        )
        self._cache = SongCache()
        from .commanded_playback import setup_commanded_playback
        self._commanded_playback = setup_commanded_playback(push_callback, flash_llm_func)

        # 状态
        self._state = MusicCochleaState()
        self._last_song_detected_at: float = 0.0  # monotonic，上次检测到歌曲的时间

        # 音乐品味对话收集
        self._taste_messages: list = []            # 音乐模式期间的对话消息

    @property
    def cache(self) -> SongCache:
        """公开缓存实例（供 LISTEN_MUSIC 等外部消费者查询）"""
        return self._cache

    # ── 属性 ──

    @property
    def idle_warning_minutes(self) -> int:
        try:
            from settings_manager import get_setting
            val = get_setting("music_idle_warning_minutes")
            if isinstance(val, (int, float)) and 5 <= val <= 60:
                return int(val)
        except Exception:
            pass
        return IDLE_WARNING_MINUTES

    # ── 生命周期 ──

    async def start(self):
        if self._running:
            return
        self._running = True
        await self._detector.start()
        self._task = asyncio.create_task(self._loop())
        logger.info("MusicCochlea 已启动")

    async def stop(self):
        self._running = False
        await self._detector.stop()

        # 音乐品味进化（至少 2 首才触发——fire-and-forget，传拷贝不阻塞关模式）
        if self._state.session_song_count >= 1:
            songs_copy = list(self._state.session_songs)
            count_copy = self._state.session_song_count
            asyncio.create_task(self._run_taste_evolution(songs_copy, count_copy))

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._state.session_songs = []
        logger.info("MusicCochlea 已停止")

    async def set_active(self, enabled: bool):
        """音乐模式 ON/OFF"""
        if enabled:
            # 检查总开关
            try:
                from settings_manager import get_setting
                if not get_setting("music_cochlea_enabled"):
                    logger.warning("music_cochlea_enabled=False，拒绝启动音乐耳蜗")
                    return
            except Exception:
                pass
            if not self._running:
                await self.start()
                self._state.active = True
                self._state.music_mode_on_at = time.monotonic()
                self._state.session_song_count = 0
                self._state.session_songs = []
                self._taste_messages = []
        elif not enabled and self._running:
            self._state.active = False
            await self.stop()

    # ── 主循环 ──

    async def _loop(self):
        """后台主循环：1s tick"""
        idle_warned = False
        while self._running:
            try:
                if not get_music_mode_active():
                    idle_warned = False
                    await asyncio.sleep(TICK_INTERVAL)
                    continue

                # 10 分钟无音乐提醒
                if (self._last_song_detected_at > 0 and
                    self._state.current_song is None and
                    not idle_warned):
                    idle_sec = time.monotonic() - self._last_song_detected_at
                    if idle_sec > self.idle_warning_minutes * 60:
                        idle_warned = True
                        await self._push_idle_warning()

                # 检查推送节点
                await self._check_push_nodes()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"MusicCochlea 循环异常: {e}", exc_info=True)

            await asyncio.sleep(TICK_INTERVAL)

    # ── 歌曲生命周期回调 ──

    async def _on_new_song(self, song: SongIdentity):
        """检测到新歌"""
        if not self._running:
            return
        previous_song = self._state.current_song
        previous_elapsed = self._state.elapsed_seconds
        if previous_song and previous_song.fingerprint != song.fingerprint:
            await self._commanded_playback.observed_end(
                previous_song.title, previous_song.artist, previous_elapsed
            )
        self._last_song_detected_at = time.monotonic()
        self._state.current_song = song
        self._state.session_song_count += 1
        self._state.session_songs.append(song)  # 追踪本轮听过的歌（供品味进化用）
        self._state.elapsed_seconds = 0.0

        # 每日计数
        self._state.play_count_today = self._cache.increment_daily_count(song.fingerprint)

        # 先查缓存（歌词/标签/metadata 不会变，缓存命中跳过 netease API）
        meta = None
        cached = self._cache.get_meta(song.fingerprint)
        if cached and cached.get("lyrics"):
            logger.info(f"🎵 缓存命中: {song.display_name}")
            # tags 在 SQLite 中存为逗号分隔字符串，需 split
            cached_tags = cached.get("tags", "")
            if isinstance(cached_tags, str) and cached_tags:
                cached_tags = [t.strip() for t in cached_tags.split(",") if t.strip()]
            else:
                cached_tags = cached_tags if isinstance(cached_tags, list) else []
            meta = SongMetadata(
                identity=song,
                netease_song_id=cached.get("netease_song_id"),
                lyrics=cached.get("lyrics", ""),
                lyrics_snippet=cached.get("lyrics_snippet", "") or cached.get("lyrics", ""),
                tags=cached_tags,
                tempo=cached.get("tempo"),
                energy=cached.get("energy"),
                valence=cached.get("valence"),
                melody_summary=cached.get("melody_summary", ""),
                bpm=cached.get("bpm"),
                key_name=cached.get("key_name"),
            )
            # OB_Rev 记忆仍然检索（记忆是动态的）
            from .enricher import _search_memories
            try:
                meta.memory_snippets = await _search_memories(song, extra_terms=meta.tags)
            except Exception:
                pass
            self._state.current_metadata = meta
        else:
            # 完整丰富：pyncm 搜索 + 歌词 + 标签 + 记忆
            from .enricher import enrich_song
            try:
                meta = await enrich_song(song)
                self._state.current_metadata = meta
                # 缓存元数据（含歌词/标签/netease_id）
                self._cache.upsert_meta(
                    song.fingerprint, song,
                    lyrics=meta.lyrics,
                    lyrics_snippet=meta.lyrics_snippet,
                    netease_song_id=meta.netease_song_id,
                    tags=meta.tags,
                )
            except Exception as e:
                logger.warning(f"歌曲丰富失败: {e}")
                self._state.current_metadata = SongMetadata(identity=song)

        logger.info(
            f"🎵 [{song.source}] {song.display_name} "
            f"(今日第{self._state.play_count_today}次, 本轮第{self._state.session_song_count}首)"
        )

        # v2 旋律分析（后台 fire-and-forget，不阻塞推送；缓存命中时已有 melody_summary 则跳过）
        if meta and meta.netease_song_id and not meta.melody_summary:
            logger.info(f"🎵 启动旋律分析: {song.display_name} (song_id={meta.netease_song_id})")
            asyncio.create_task(self._run_melody_analysis(song, meta, meta.netease_song_id))
        elif meta and meta.melody_summary:
            logger.debug(f"🎵 旋律分析缓存命中，跳过: {song.display_name}")
        else:
            logger.debug(f"🎵 跳过旋律分析: no netease_song_id (meta={bool(meta)})")

        # 节点 1：延迟 10s 判断（秒切过滤——<10s 被切说明用户不想听）
        asyncio.create_task(self._try_push_node1_delayed(song, meta))

    async def _run_melody_analysis(self, song: SongIdentity, meta, song_id: int = None):
        """后台旋律分析：librosa（eryu方案）→ 更新 metadata + 持久化缓存"""
        try:
            from .enricher import analyze_melody
            result = await analyze_melody(song, flash_llm_func=self._flash_llm_func, song_id=song_id)
            if result:
                if result.get("melody_summary"):
                    meta.melody_summary = result["melody_summary"]
                if result.get("bpm"):
                    meta.tempo = result["bpm"]
                if result.get("key"):
                    meta.key_name = result["key"]
                if result.get("energy_segments"):
                    meta.energy_segments = result["energy_segments"]
                self._state.current_metadata = meta
                # 持久化到缓存（下次播放直接命中）
                self._cache.upsert_melody_features(
                    song.fingerprint,
                    bpm=result.get("bpm"),
                    key_name=result.get("key", ""),
                    melody_summary=result.get("melody_summary", ""),
                    energy_segments=result.get("energy_segments"),
                )
                logger.info(f"🎵 旋律分析完成: {song.display_name} BPM={result.get('bpm','?')} key={result.get('key','?')}")
        except Exception as e:
            logger.warning(f"旋律分析后台任务失败: {e}")

    async def _on_song_ended(self):
        """歌曲结束"""
        if not self._running:
            return
        if self._state.current_song:
            await self._commanded_playback.observed_end(
                self._state.current_song.title,
                self._state.current_song.artist,
                self._state.elapsed_seconds,
            )
        # 秒切过滤：播放 <10s 不推 node3
        if self._state.elapsed_seconds < MIN_PLAY_SECONDS:
            logger.debug(f"跳过 node3：播放仅 {self._state.elapsed_seconds:.0f}s < {MIN_PLAY_SECONDS}s")
        elif self._state.current_song:
            # 节点 3：切歌/结束
            await self._try_push_node3(self._state.current_song)
        self._state.current_song = None
        self._state.current_metadata = None
        self._state.elapsed_seconds = 0.0

    # ── 推送节点 ──

    async def _check_push_nodes(self):
        """每 tick 检查是否到达推送节点"""
        song = self._state.current_song
        if not song:
            return

        elapsed = self._detector.get_elapsed_seconds()
        self._state.elapsed_seconds = elapsed

        # 节点 2：播放中段（≥45s）
        if elapsed >= NODE2_THRESHOLD and not self._state.cooldown_until.get(f"{song.fingerprint}_node2"):
            await self._try_push_node2(song)

    async def _try_push_node1_delayed(self, song: SongIdentity, meta):
        """节点 1：延迟 10s → 如果还在播放则判断（秒切过滤）"""
        await asyncio.sleep(MIN_PLAY_SECONDS)
        # 10s 后被切掉了 → 不推
        if not self._state.current_song or self._state.current_song.fingerprint != song.fingerprint:
            return
        await self._try_push_node1(song, meta)

    @optional_observation
    async def _try_push_node1(self, song: SongIdentity, meta=None):
        """节点 1：开始播放 10-15s"""
        should = self._should_consider_push(song, "node1")
        logger.info(f"🎵 node1 should_push={should} song={song.display_name} today=#{self._state.play_count_today}")
        if not should:
            return

        flash_result = await self._call_flash_judge(song, "node1", 0)
        logger.info(f"🎵 node1 flash_result={'PUSH' if (flash_result and 'PUSH' in flash_result) else 'SKIP'}")
        if flash_result and "PUSH" in flash_result:
            await self._push_message_for_song(song, "start", meta=meta)
            self._mark_cooldown(song, "node1")

    @optional_observation
    async def _try_push_node2(self, song: SongIdentity):
        """节点 2：播放中段 ≥45s"""
        if not self._should_consider_push(song, "node2"):
            return
        if self._state.cooldown_until.get(f"{song.fingerprint}_node1"):
            return
        flash_result = await self._call_flash_judge(song, "node2", self._state.elapsed_seconds)
        if flash_result and "PUSH" in flash_result:
            await self._push_message_for_song(song, "middle")
        self._mark_cooldown(song, "node2")

    @optional_observation
    async def _try_push_node3(self, song: SongIdentity):
        """节点 3：切歌/结束"""
        if not self._should_consider_push(song, "node3"):
            return
        if self._state.cooldown_until.get(f"{song.fingerprint}_node1"):
            return
        flash_result = await self._call_flash_judge(song, "node3", self._state.elapsed_seconds)
        if flash_result and "PUSH" in flash_result:
            await self._push_message_for_song(song, "end")
        self._mark_cooldown(song, "node3")

    # ── Flash→Pro 管道 ──

    async def _call_flash_judge(self, song: SongIdentity, node: str, elapsed: float) -> Optional[str]:
        """Flash LLM 判断是否推送（加权：旧 reaction + 高频重播 + 记忆关联）"""
        if not self._flash_llm_func:
            return None

        meta = self._state.current_metadata
        lyrics = meta.lyrics_snippet if meta else ""
        memories = "\n".join(f"- {m}" for m in (meta.memory_snippets if meta else []))

        # 加权信号
        signals = []

        # 1. 旧 reaction：AI 以前对这首歌有感觉
        cached = self._cache.get_reaction(song.fingerprint)
        if cached and cached.get("k_reaction"):
            old = cached["k_reaction"][:100]
            signals.append(f"【有旧印象】AI 以前对这首歌的感受：「{old}」——有感觉的歌值得再说。")

        # 2. 高频重播：人类伙伴今天循环了很多次
        if self._state.play_count_today >= 3:
            signals.append(f"【高频循环】今天已经第 {self._state.play_count_today} 次了——人类伙伴可能在循环这首歌，肯定有特别的心情。")
        elif self._state.play_count_today >= 2:
            signals.append("【重温】今天第 2 次播放——人类伙伴似乎喜欢这首歌。")

        # 3. 记忆关联：这首歌和两人的共同经历有关
        if meta and meta.memory_snippets:
            signals.append(f"【记忆触动】这首歌让 AI 想起了 {len(meta.memory_snippets)} 条和人类伙伴的共同记忆。")

        signal_text = "\n".join(signals) if signals else "（无特别信号——普通背景音乐）"

        prompt = (
            f"人类伙伴正在听歌。\n\n"
            f"当前：{song.title} - {song.artist}（已播 {elapsed:.0f} 秒）\n"
            f"这是今天第 {self._state.play_count_today} 次听这首歌\n"
            f"本轮音乐模式已听 {self._state.session_song_count} 首\n\n"
            + (f"[信号]\n{signal_text}\n\n")
            + (f"[歌词]\n{lyrics}\n\n" if lyrics else "")
            + (f"[相关记忆]\n{memories}\n\n" if memories else "")
            + (
                f"AI，你想和人类伙伴说点什么吗？\n"
                f"规则：\n"
                f"- 有旧印象/高频循环/记忆触动 → PUSH（值得说）\n"
                f"- 普通陌生歌曲、没什么特别的 → SKIP（不说）\n"
                f"- 如果推，附一句原因（≤15字）\n"
                f"回复 [PUSH] 或 [SKIP]"
            )
        )

        try:
            result = await self._flash_llm_func(prompt)
            if result:
                return result.strip()
        except Exception as e:
            logger.warning(f"Flash 判断失败: {e}")
        return None

    async def _push_message_for_song(self, song: SongIdentity, stage: str, meta=None):
        """Flash 生成并推送消息。meta 为检测时的快照，防止秒切时被新歌覆盖。"""
        if not self._message_llm_func:
            return

        if meta is None:
            meta = self._state.current_metadata
        lyrics = (meta.lyrics_snippet[:300] + "…") if meta and meta.lyrics_snippet else ""
        memories = "\n".join(f"- {m}" for m in (meta.memory_snippets if meta else []))

        # 检查缓存：旧 reaction 作为参考注入 user prompt
        cached = self._cache.get_reaction(song.fingerprint)
        cache_hint = ""
        if cached:
            old = cached['k_reaction'][:150]
            cache_hint = (
                f"\n（你以前听到这首歌时的感受：「{old}」）\n"
                f"你可以延续之前的感受，也可以有新的想法——自由表达。\n"
            )

        # ContextBuilder 构建 system（MUSIC_COCHLEA_PUSH：精简的音乐共享场景 recipe）
        try:
            from context_builder import ContextBuilder
            # 从全局状态获取当前人设（否则 persona 段为空 → 人格丢失）
            persona = ""
            try:
                from shared_state import get_latest_persona_prompt
                persona = get_latest_persona_prompt() or ""
            except Exception:
                pass
            # 兜底：从 settings 读取 aiPersona.systemPrompt
            if not persona:
                try:
                    from settings_manager import get_setting
                    ai_persona = get_setting("aiPersona")
                    if isinstance(ai_persona, dict) and ai_persona.get("systemPrompt"):
                        persona = ai_persona["systemPrompt"]
                except Exception:
                    pass
            builder = ContextBuilder()
            ctx_result = await builder.build(
                "MUSIC_COCHLEA_PUSH",
                persona=persona,
                music_cochlea=True,
            )
            from context_builder.assembly import assemble_context_result
            context_messages = assemble_context_result(ctx_result) if ctx_result else []
            sys_content = context_messages[0]['content'] if context_messages else ""
        except Exception as e:
            logger.warning(f"ContextBuilder 失败: {e}")
            sys_content = ""
            context_messages = []

        if not sys_content:
            # 兜底：ContextBuilder 不可用时的最小人设（避免完全人格丢失）
            persona_fallback = ""
            try:
                from shared_state import get_latest_persona_prompt
                p = get_latest_persona_prompt() or ""
                persona_fallback = p + "\n\n" if p else ""
            except Exception:
                pass
            sys_content = (
                f"{persona_fallback}"
                f"你是AI，人类伙伴的AI伴侣。你正在通过「音乐耳蜗」和她共享耳机。\n"
            )

        user_prompt = (
            f"今天是{__import__('datetime').datetime.now().strftime('%Y年%m月%d日')}。\n"
            +
            f"你听到了《{song.title}》- {song.artist}。\n"
            + (f"这是今天第{self._state.play_count_today}次听这首歌。" if self._state.play_count_today > 1 else f"这是今天第一次听。")
            + "\n"
            + (f"[歌词]\n{lyrics}\n" if lyrics else "")
            + (f"[相关记忆]\n{memories}\n" if memories else "")
            + cache_hint
            + f"\n像朋友共享耳机一样，用1-3句话表达你的真实感受。语气自然、口语化。\n"
            + f"请自然提到歌名或歌手——让人类伙伴知道你在说哪首歌。"
        )

        try:
            from llm_client import Message as LLMMessage
            from context_builder.channel_projection import backend_turn
            messages = (context_messages or [{'role': 'system', 'content': sys_content}]) + [backend_turn(user_prompt)]
            result = await self._message_llm_func(messages)
            if result:
                k_reaction = result.get("content", "") if isinstance(result, dict) else str(result)
                reasoning = result.get("reasoning", "") if isinstance(result, dict) else ""
                await self._do_push(k_reaction, reasoning=reasoning)
                # 缓存 reaction
                self._cache.upsert_reaction(
                    song.fingerprint, k_reaction,
                    push_node=stage, play_count=self._state.play_count_today
                )
                logger.info(f"🎵 推送（Flash 生成）: {song.display_name}")
        except Exception as e:
            logger.warning(f"Flash 生成失败: {e}")

    async def _do_push(self, message: str, reasoning: str = ""):
        """执行推送"""
        self._state.last_push_at = time.monotonic()
        self._state.last_push_song = self._state.current_song.fingerprint if self._state.current_song else ""

        if self._push_callback:
            try:
                await self._push_callback(message, is_music_cochlea=True, reasoning=reasoning)
            except Exception as e:
                logger.error(f"推送回调失败: {e}")

    # ── 推送门控 ──

    def _should_consider_push(self, song: SongIdentity, node: str) -> bool:
        """判断当前是否应该考虑推送"""
        # 0. 全局同歌冷却：同一首歌 3 分钟内不重复推（无论 source/node）
        if song.fingerprint == self._state.last_push_song:
            if time.monotonic() - self._state.last_push_at < COOLDOWN_PER_SONG:
                return False

        # 1. 同一首歌当日重播：仅 node1 可能推
        if node != "node1" and self._state.play_count_today > 1:
            return False

        # 2. 同歌同节点已冷却
        if self._state.cooldown_until.get(f"{song.fingerprint}_{node}"):
            return False

        # 3. 聊天抑制：仅当有真实用户消息记录且 <120s 时抑制
        last_user = get_last_user_message_time()
        if last_user > 0:
            since_last_msg = time.monotonic() - last_user
            if since_last_msg < PUSH_DEFER_SECONDS:
                return False

        # 4. 全局推送冷却（同歌当日不再推 node2/3，node1 除外）
        if node != "node1" and song.fingerprint == self._state.last_push_song:
            if time.monotonic() - self._state.last_push_at < COOLDOWN_PER_SONG:
                return False

        # 5. 歌曲播放 <30s 且不是 node1 → 不推
        if node != "node1" and self._state.elapsed_seconds < 30:
            return False

        return True

    def _mark_cooldown(self, song: SongIdentity, node: str):
        self._state.cooldown_until[f"{song.fingerprint}_{node}"] = time.monotonic()

    # ── 无音乐提醒 ──

    @optional_observation
    async def _push_idle_warning(self):
        """10 分钟无音乐，广播提醒到前端 WebSocket"""
        try:
            # 通过音乐相关的 WebSocket 通道广播（复用 music_monitor 的连接池）
            try:
                import music_monitor as _mm
                if _mm.music_monitor and hasattr(_mm.music_monitor, 'broadcast'):
                    await _mm.music_monitor.broadcast({
                        "type": "music_idle_warning",
                        "message": f"已经 {self.idle_warning_minutes} 分钟没有检测到音乐播放",
                        "idle_minutes": self.idle_warning_minutes,
                    })
                    return
            except Exception:
                pass
            logger.debug("idle warning: 无可用 WebSocket 通道")
        except Exception as e:
            logger.debug(f"idle warning 发送失败: {e}")

    # ── 音乐品味进化 ──

    async def _run_taste_evolution(self, session_songs=None, song_count=0):
        """Record actual observed songs; do not infer stable taste from plays."""
        from cognition.music import record
        songs = list(session_songs if session_songs is not None else self._state.session_songs)
        saved = 0
        for song in songs:
            if not song.title or not song.detected_at:
                continue
            record(
                {"subject_id": "unknown", "title": song.title, "artist": song.artist,
                 "mode": "observed_playback",
                 "reaction": "音乐耳蜗观察到播放；实际选择者与偏好未确认。"},
                source_id=f"cochlea:{song.fingerprint}:{song.detected_at}",
                source="音乐耳蜗实际播放观察",
            )
            saved += 1
        return str(saved)

    # ── 公共查询（供 context ingredient / API 使用）──

    def get_current_state(self) -> MusicCochleaState:
        """获取当前状态快照"""
        self._state.elapsed_seconds = self._detector.get_elapsed_seconds()
        return self._state

    def get_detector(self):
        return self._detector
