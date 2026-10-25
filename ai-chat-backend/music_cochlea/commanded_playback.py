"""Lifecycle for songs explicitly started by AI through ``cloud_music``.

Manual listening remains observational.  Only tool-started songs are paused at
their expected end and returned to AI (Flash) for the next explicit decision.
"""

from __future__ import annotations

import asyncio
from body_admission import optional_observation
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class CommandedSong:
    device: str
    song: dict
    candidates: list[dict] = field(default_factory=list)


class CommandedPlaybackCoordinator:
    def __init__(self, push_callback: Callable = None, flash_llm_func: Callable = None):
        self._push_callback = push_callback
        self._flash_llm_func = flash_llm_func
        self._current: Optional[CommandedSong] = None
        self._timer: Optional[asyncio.Task] = None

    def register(self, device: str, song: dict, candidates: list[dict] | None = None) -> None:
        self.cancel()
        self._current = CommandedSong(device=device, song=dict(song), candidates=list(candidates or []))
        duration_ms = int(song.get("duration") or song.get("duration_ms") or 0)
        if duration_ms <= 0:
            logger.warning("AI 点播歌曲缺少时长，无法建立可靠的单曲结束计时: %s", song.get("name"))
            return
        self._timer = asyncio.create_task(self._wait_for_expected_end(duration_ms / 1000.0))
        logger.info("AI 单曲托管已建立: device=%s song=%s duration=%sms",
                    device, song.get("name"), duration_ms)

    def update_candidates(self, candidates: list[dict]) -> None:
        if self._current:
            self._current.candidates = list(candidates or [])

    def cancel(self) -> None:
        task = self._timer
        self._timer = None
        self._current = None
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def observed_end(self, title: str, artist: str, elapsed_seconds: float) -> None:
        current = self._current
        if not current or not self._matches(current.song, title, artist):
            return
        duration = int(current.song.get("duration") or 0) / 1000.0
        if duration and elapsed_seconds + 12 < duration:
            # The listener disappeared well before the expected end: a manual stop,
            # app exit, or an unreliable observation is not a natural completion.
            logger.info("AI 单曲托管取消：提前结束 %.1fs/%.1fs", elapsed_seconds, duration)
            self.cancel()
            return
        await self._complete(current)

    async def _wait_for_expected_end(self, duration_seconds: float) -> None:
        try:
            await asyncio.sleep(max(5.0, duration_seconds + 1.5))
            current = self._current
            if current:
                await self._complete(current)
        except asyncio.CancelledError:
            return

    async def _complete(self, current: CommandedSong) -> None:
        if self._current is not current:
            return
        self._timer = None
        self._current = None

        # NetEase may have already auto-started its queue. Pause first so AI, not
        # the app playlist, owns the next action.
        await self._control(current.device, "pause")
        await self._continue_after_pause(current)

    @optional_observation
    async def _continue_after_pause(self, current: CommandedSong) -> None:
        decision = await self._ask_k(current)
        action = decision.get("action", "pause")
        query = str(decision.get("query", "")).strip()
        message = str(decision.get("message", "")).strip()

        if action == "play_song" and query:
            play_result = await self._control(current.device, "search_play", query=query)
            if not getattr(play_result, "error", None):
                if message:
                    await self._push(message)
                return
            logger.warning("AI 续播失败: %s", getattr(play_result, "error", "unknown"))

        if not message:
            name = current.song.get("name", "这首歌")
            message = f"《{name}》听完了，我先让它停在这里。"
        await self._push(message)

    async def _ask_k(self, current: CommandedSong) -> dict:
        if not self._flash_llm_func:
            return {"action": "pause", "query": "", "message": ""}
        choices = []
        for song in current.candidates[:4]:
            name = song.get("name", "")
            artist = song.get("artist", "")
            if name:
                choices.append(f"- {name}" + (f" - {artist}" if artist else ""))
        candidate_text = "\n".join(choices) if choices else "（没有预选候选；若续播，需要自行给出明确歌曲名和歌手）"
        persona = ""
        try:
            from shared_state import get_latest_persona_prompt
            persona = (get_latest_persona_prompt() or "")[:1800]
        except Exception:
            pass
        prompt = (
            f"{persona}\n\n" if persona else ""
        ) + (
            "[真实工具结果]\n"
            f"AI 刚才点播的《{current.song.get('name', '')}》- {current.song.get('artist', '')}已经播放结束，"
            f"播放设备是{'手机' if current.device == 'mobile' else '电脑'}。网易云已暂停，等待 AI 决定。\n\n"
            f"可参考的下一首候选：\n{candidate_text}\n\n"
            "请作为 AI 决定：停在这里，或明确点播一首下一首歌曲。只输出 JSON："
            '{"action":"pause|play_song","query":"下一首歌名 歌手；pause 时为空","message":"给人类伙伴的一句自然短消息"}'
            "。选择 play_song 时 query 必须包含可搜索的歌曲信息，不能只写‘下一首’。"
        )
        try:
            raw = await self._flash_llm_func(prompt)
            text = raw.get("content", "") if isinstance(raw, dict) else str(raw or "")
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(text)
            if parsed.get("action") == "play_song" and str(parsed.get("query", "")).strip():
                return parsed
            return {"action": "pause", "query": "", "message": parsed.get("message", "")}
        except Exception as exc:
            logger.warning("AI 单曲结束决策失败，保持暂停: %s", exc)
            return {"action": "pause", "query": "", "message": ""}

    async def _control(self, device: str, action: str, query: str = ""):
        from behavior_scheduler.cloud_music_tool import CloudMusicTool
        from behavior_scheduler.execution_context import reset_request_platform, set_request_platform
        token = set_request_platform(device)
        try:
            return await CloudMusicTool().execute(action=action, query=query)
        finally:
            reset_request_platform(token)

    async def _push(self, message: str) -> None:
        if self._push_callback and message:
            await self._push_callback(message, is_music_cochlea=True)

    @staticmethod
    def _matches(song: dict, title: str, artist: str) -> bool:
        expected_title = str(song.get("name", "")).strip().lower()
        expected_artist = str(song.get("artist", "")).strip().lower()
        actual_title = str(title or "").strip().lower()
        actual_artist = str(artist or "").strip().lower()
        return expected_title == actual_title and (
            not expected_artist or not actual_artist
            or expected_artist in actual_artist or actual_artist in expected_artist
        )


_coordinator: Optional[CommandedPlaybackCoordinator] = None


def setup_commanded_playback(push_callback=None, flash_llm_func=None) -> CommandedPlaybackCoordinator:
    global _coordinator
    _coordinator = CommandedPlaybackCoordinator(push_callback, flash_llm_func)
    return _coordinator


def get_commanded_playback() -> Optional[CommandedPlaybackCoordinator]:
    return _coordinator
