"""
歌曲检测器 — PC 窗口标题 + 手机 MediaSession 事件接收

PC 端：复用 music_monitor.py 的 win32 窗口标题解析
手机端：接收 WebSocket 推送的 now_playing 事件
双端融合：按用户设置优先级，去重
"""

import asyncio
import time
import logging
from typing import Optional, Callable, Dict
from datetime import datetime

from .models import SongIdentity

logger = logging.getLogger(__name__)

# ── 尝试导入 win32 依赖（PC 端需要，手机端 backend 可能没有）──
try:
    import win32gui
    import win32process
    import psutil
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False
    logger.info("win32gui/psutil 不可用，PC 窗口标题检测将不可用")

PROCESS_NAME = "cloudmusic.exe"
DEBOUNCE_SECONDS = 2.0       # 窗口标题变化去抖
SAME_SONG_DEBOUNCE = 5.0     # 同一首歌 5s 内不重复上报


class SongDetector:
    """双平台歌曲检测器

    PC：每 1s 检查一次 cloudmusic.exe 窗口标题
    手机：await receive_mobile_event() 接收 MediaSession 推送
    """

    def __init__(self, on_song_change: Callable = None,
                 on_song_end: Callable = None,
                 source_priority: str = "mobile"):
        self._on_song_change = on_song_change  # async fn(SongIdentity)
        self._on_song_end = on_song_end          # async fn()
        self._source_priority = source_priority   # "mobile" | "pc"

        # 当前状态
        self._current_song: Optional[SongIdentity] = None
        self._song_started_at: float = 0.0       # monotonic
        self._last_title: str = ""
        self._last_title_change: float = 0.0      # monotonic
        self._last_song_fingerprint: str = ""

        # 手机端状态
        self._mobile_song: Optional[SongIdentity] = None
        self._mobile_song_started_at: float = 0.0

        # 后台任务
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._started_at: float = 0.0             # monotonic, 启动时间
        self._startup_grace: float = 3.0           # 启动后 3s 内忽略检测（防窗口标题残留）

    # ── 生命周期 ──

    async def start(self):
        self._running = True
        self._started_at = time.monotonic()
        # 启动时记录当前标题作为基准，只监听"变化"
        # 但如果已经在播歌，记录为 current_song（供上下文注入，不推送）
        if _WIN32_AVAILABLE:
            try:
                hwnd = self._find_cloudmusic_window()
                if hwnd:
                    title = win32gui.GetWindowText(hwnd)
                    self._last_title = title
                    parsed = self._parse_title(title)
                    if parsed.get("song"):
                        self._current_song = SongIdentity(
                            title=parsed["song"].strip(),
                            artist=parsed.get("artist", "").strip(),
                            source="pc",
                        )
                        self._song_started_at = time.monotonic()
                        logger.info(f"SongDetector 启动时已检测到播放: {self._current_song.display_name}（不触发推送）")
            except Exception:
                pass
        self._task = asyncio.create_task(self._pc_poll_loop())
        logger.info("SongDetector 已启动（PC 窗口标题轮询）")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("SongDetector 已停止")

    def set_source_priority(self, priority: str):
        self._source_priority = priority

    # ── PC 轮询 ──

    async def _pc_poll_loop(self):
        """每 1s 检查一次 cloudmusic.exe 窗口标题"""
        while self._running:
            try:
                if _WIN32_AVAILABLE:
                    self._check_pc_window()
            except Exception as e:
                logger.debug(f"PC 窗口检测异常: {e}")
            await asyncio.sleep(1.0)

    def _check_pc_window(self):
        """检查网易云窗口标题，检测歌曲变化"""
        if not _WIN32_AVAILABLE:
            return

        # 启动后 3s 内忽略（防残留窗口标题触发）
        if time.monotonic() - self._started_at < self._startup_grace:
            return

        try:
            hwnd = self._find_cloudmusic_window()
            if not hwnd:
                # 窗口不存在 → 如果之前有歌在播，触发结束
                if self._current_song and self._current_song.source == "pc":
                    self._on_pc_song_ended()
                return

            title = win32gui.GetWindowText(hwnd)
        except Exception:
            return

        if not title or title == self._last_title:
            return

        self._last_title = title
        self._last_title_change = time.monotonic()

        parsed = self._parse_title(title)
        if not parsed.get("song"):
            # 窗口存在但不在播放（如"网易云音乐"默认标题）
            if self._current_song and self._current_song.source == "pc":
                self._on_pc_song_ended()
            return

        # 构建 SongIdentity
        song = SongIdentity(
            title=parsed["song"].strip(),
            artist=parsed.get("artist", "").strip(),
            source="pc",
            detected_at=datetime.now().isoformat()
        )

        # 去重：同一首歌
        if song.fingerprint == self._last_song_fingerprint:
            elapsed = time.monotonic() - self._last_title_change
            if elapsed < SAME_SONG_DEBOUNCE:
                return  # 窗口重绘导致的重复标题

        # 新歌！
        self._last_song_fingerprint = song.fingerprint
        self._current_song = song
        self._song_started_at = time.monotonic()
        logger.info(f"[PC] 检测到新歌: {song.display_name}")

        if self._on_song_change:
            asyncio.get_event_loop().create_task(self._on_song_change(song))

    def _on_pc_song_ended(self):
        """PC 端歌曲结束"""
        if self._on_song_end:
            asyncio.get_event_loop().create_task(self._on_song_end())
        self._current_song = None
        self._last_song_fingerprint = ""

    # ── 手机端事件接收 ──

    async def receive_mobile_event(self, data: dict):
        """接收手机端 now_playing 事件

        data 格式（来自 MirrowRelayService MediaSession）：
        {
            "title": "歌名",
            "artist": "歌手",
            "album": "专辑（可选）",
            "playing": true,    # false = 暂停
            "position": 45000   # 播放位置（毫秒）
        }
        """
        playing = data.get("playing", True)
        if not playing:
            # 暂停
            if self._current_song and self._current_song.source == "mobile":
                # 保持 current_song 但标记暂停（上下文注入用）
                logger.debug(f"[Mobile] 暂停: {data.get('title', '?')}")
            return

        title = data.get("title", "").strip()
        artist = data.get("artist", "").strip()
        if not title or not artist:
            return

        song = SongIdentity(
            title=title,
            artist=artist,
            album=data.get("album"),
            source="mobile",
            detected_at=datetime.now().isoformat()
        )

        # 去重
        if song.fingerprint == self._last_song_fingerprint:
            return

        self._last_song_fingerprint = song.fingerprint
        self._mobile_song = song
        self._mobile_song_started_at = time.monotonic()

        # 判断是否切换为当前活跃歌曲
        if self._should_activate_mobile():
            self._current_song = song
            self._song_started_at = self._mobile_song_started_at

        logger.info(f"[Mobile] 检测到新歌: {song.display_name}")

        if self._on_song_change:
            asyncio.get_event_loop().create_task(self._on_song_change(song))

    def _should_activate_mobile(self) -> bool:
        """判断手机端歌曲是否应成为当前活跃歌曲"""
        if self._source_priority == "mobile":
            return True  # 手机优先 → 永远激活
        if self._source_priority == "pc":
            return self._current_song is None  # PC 优先 → 仅 PC 没歌时激活
        return True

    # ── 公共查询 ──

    def get_current_song(self) -> Optional[SongIdentity]:
        return self._current_song

    def get_elapsed_seconds(self) -> float:
        """当前歌曲已播放秒数"""
        if not self._current_song:
            return 0.0
        return time.monotonic() - self._song_started_at

    def get_state_summary(self) -> dict:
        """返回给 /api/music/cochlea/status 的状态摘要"""
        song = self._current_song
        return {
            "current_song": {
                "title": song.title,
                "artist": song.artist,
            } if song else None,
            "elapsed_seconds": self.get_elapsed_seconds() if song else 0,
            "source": song.source if song else "",
            "pc_has_window": self._check_pc_window_quick() if _WIN32_AVAILABLE else False,
        }

    # ── 静态工具方法（复用自 music_monitor.py）──

    @staticmethod
    def _find_cloudmusic_window() -> Optional[int]:
        if not _WIN32_AVAILABLE:
            return None
        result = []
        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if psutil.Process(pid).name().lower() == PROCESS_NAME:
                    title = win32gui.GetWindowText(hwnd)
                    if title and len(title) > 1:
                        result.append(hwnd)
                        return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            return True
        try:
            win32gui.EnumWindows(callback, None)
        except Exception:
            pass
        return result[0] if result else None

    @staticmethod
    def _parse_title(title: str) -> Dict[str, str]:
        if " - " in title:
            parts = title.split(" - ", 1)
            return {"title": title, "song": parts[0].strip(), "artist": parts[1].strip()}
        return {"title": title, "song": "", "artist": ""}

    def _check_pc_window_quick(self) -> bool:
        return self._find_cloudmusic_window() is not None
