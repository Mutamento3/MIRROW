# 网易云音乐播放状态监控
# 通过 Win32 事件钩子实时监听窗口标题变化，检测播放中断

import time
import threading
import asyncio
import logging
from typing import Optional, Callable, Set, Dict, Any

logger = logging.getLogger(__name__)

try:
    import win32gui
    import win32process
    import win32con
    import psutil
    from ctypes import windll, c_int, WINFUNCTYPE
    _MONITOR_DEPS_AVAILABLE = True
except ImportError:
    _MONITOR_DEPS_AVAILABLE = False

PROCESS_NAME = "cloudmusic.exe"
EVENT_OBJECT_NAMECHANGE = 0x800C
WINEVENT_OUTOFCONTEXT = 0x0000


class MusicMonitor:
    """网易云音乐播放状态监控器"""

    def __init__(self):
        self._hook = None
        self._proc_ref = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._music_mode: bool = False
        self._last_title: str = ""
        self._last_song_title: str = ""
        self._last_non_song_time: float = 0.0
        self._interrupted: bool = False
        self._current_song: Dict[str, str] = {}

        self._on_interrupt: Optional[Callable] = None
        self._connections: Set[Any] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 自动注册为全局单例
        import sys
        mod = sys.modules.get(__name__)
        if mod:
            mod.music_monitor = self

    @property
    def music_mode(self) -> bool:
        return self._music_mode

    def set_music_mode(self, on: bool):
        was_on = self._music_mode
        self._music_mode = on
        # 单向同步到 shared_state，确保 MusicCochlea 等消费者感知到状态变化
        try:
            from shared_state import set_music_mode_active
            set_music_mode_active(on)
        except Exception:
            pass
        if on and not was_on:
            self._interrupted = False
            self._last_non_song_time = 0.0
            logger.info("音乐模式已开启，开始监听播放中断")
        elif not on and was_on:
            self._interrupted = True
            logger.info("音乐模式已关闭，停止监听播放中断")

    def get_now_playing(self) -> Dict[str, str]:
        """同步获取当前播放歌曲信息"""
        try:
            win = self._find_cloudmusic_window()
            if win:
                try:
                    title = win32gui.GetWindowText(win)
                    return self._parse_title(title)
                except Exception:
                    pass
        except Exception:
            pass
        return {}

    def add_connection(self, ws):
        self._connections.add(ws)

    def remove_connection(self, ws):
        self._connections.discard(ws)

    async def broadcast(self, msg: dict):
        """广播消息到所有 /ws/music 连接"""
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)

    # ========== 启动/停止 ==========

    def start(self, on_interrupt: Callable = None):
        """启动窗口标题监听线程"""
        if not _MONITOR_DEPS_AVAILABLE:
            logger.warning("win32gui/psutil 不可用，MusicMonitor 无法启动")
            return

        self._on_interrupt = on_interrupt
        self._loop = asyncio.get_event_loop()
        self._running = True

        # 获取初始状态
        current = self.get_now_playing()
        if current.get("song"):
            self._last_song_title = current.get("title", "")
            self._current_song = current

        self._thread = threading.Thread(target=self._watch_loop, daemon=True, name="music-monitor")
        self._thread.start()
        logger.info("MusicMonitor 已启动")

    def stop(self):
        """停止监听"""
        self._running = False
        if self._hook:
            try:
                windll.user32.UnhookWinEvent(self._hook)
            except Exception:
                pass
            self._hook = None
        logger.info("MusicMonitor 已停止")

    # ========== 内部实现 ==========

    def _watch_loop(self):
        """在后台线程中运行 Win32 消息泵"""
        try:
            WinEventProc = WINFUNCTYPE(None, c_int, c_int, c_int, c_int, c_int, c_int, c_int)
            self._proc_ref = WinEventProc(self._on_win_event)

            self._hook = windll.user32.SetWinEventHook(
                EVENT_OBJECT_NAMECHANGE,
                EVENT_OBJECT_NAMECHANGE,
                0,
                self._proc_ref,
                0, 0,
                WINEVENT_OUTOFCONTEXT,
            )

            if not self._hook:
                logger.error("SetWinEventHook 失败")
                return

            # 消息泵（PumpWaitingMessages 不阻塞，需短暂休眠防 CPU 空转）
            while self._running:
                try:
                    win32gui.PumpWaitingMessages()
                    time.sleep(0.01)
                except Exception:
                    break
        except Exception as e:
            logger.error(f"MusicMonitor 监听线程异常: {e}")

    def _on_win_event(self, hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        """WinEvent 回调 —— 在后台线程中调用"""
        if idObject != 0 or idChild != 0:
            return
        if not self._running:
            return

        # 验证进程
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() != PROCESS_NAME:
                return
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return

        try:
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            return

        if not title or title == self._last_title:
            return

        self._last_title = title
        parsed = self._parse_title(title)

        if parsed.get("song"):
            # 正在播放歌曲
            self._last_song_title = title
            self._current_song = parsed
            self._interrupted = False
            self._last_non_song_time = 0.0
        elif self._last_song_title and not self._interrupted and self._music_mode:
            # 从播放状态变为非播放状态 → 可能是中断
            self._last_non_song_time = time.time()
            # 3 秒后确认
            timer = threading.Timer(3.0, self._check_interrupt)
            timer.daemon = True
            timer.start()

    def _check_interrupt(self):
        """确认播放中断（在 timer 线程中调用）"""
        if not self._music_mode or self._interrupted:
            return
        if self._last_non_song_time == 0.0:
            return

        elapsed = time.time() - self._last_non_song_time
        if elapsed < 2.5:
            return

        # 再次确认窗口标题
        current = self.get_now_playing()
        if current.get("song"):
            return  # 恢复播放了

        self._interrupted = True
        song_info = self._current_song.copy()

        # 投递到主事件循环
        if self._loop and self._on_interrupt:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._on_interrupt(song_info), self._loop
                )
            except Exception as e:
                logger.error(f"投递中断回调失败: {e}")

    @staticmethod
    def _find_cloudmusic_window() -> Optional[int]:
        """静态窗口查找（可在任意线程调用）"""
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
        """解析窗口标题为歌曲信息"""
        if " - " in title:
            parts = title.split(" - ", 1)
            return {"title": title, "song": parts[0].strip(), "artist": parts[1].strip()}
        return {"title": title, "song": "", "artist": ""}


# 全局单例（由 main.py 初始化）
music_monitor: Optional[MusicMonitor] = None
