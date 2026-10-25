"""屏幕画面变化数据源 — 比较相邻采样，不把静态画面当作操作。"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, Any

from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)


class ScreenActivitySource(DataSource):
    """屏幕画面变化数据源 — 相邻采样比较，单帧方差仅作诊断。"""

    name = "screen_activity"

    def __init__(self):
        self._previous_frame = None
        self._previous_frame_at = None
        self._mss_available = False
        try:
            import mss
            import numpy as np
            self._mss = mss
            self._np = np
            self._mss_available = True
        except ImportError:
            logger.warning("ScreenActivitySource: mss/numpy 不可用")

    async def read(self) -> DataPoint:
        if not self._mss_available:
            return DataPoint(source=self.name)
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._capture)
            return DataPoint(
                source=self.name,
                data=result,
                captured_at=datetime.now().isoformat(),
            )
        except Exception as e:
            self._previous_frame = None
            self._previous_frame_at = None
            logger.debug(f"ScreenActivitySource 读取失败: {e}")
            return DataPoint(source=self.name)

    def _frame_change(self, gray, now=None):
        """Only a comparable, recent pair proves temporal change.

        Keep a downsampled grayscale frame in memory only. Small cursor/clock
        changes do not count as broad screen activity; none of this proves a
        person is operating the computer.
        """
        now = time.monotonic() if now is None else now
        frame = gray[::8, ::8].copy()
        previous = self._previous_frame
        previous_at = self._previous_frame_at
        self._previous_frame, self._previous_frame_at = frame, now
        if (previous is None or previous.shape != frame.shape
                or previous_at is None or not 0 < now - previous_at <= 180):
            return None, None, None
        delta = self._np.abs(frame - previous)
        mean_delta = float(self._np.mean(delta))
        changed_fraction = float(self._np.mean(delta > 10))
        return mean_delta > 2 and changed_fraction > 0.01, mean_delta, changed_fraction

    def _capture(self) -> Dict[str, Any]:
        with self._mss.mss() as sct:
            monitor = sct.monitors[0]
            img = sct.grab(monitor)
            arr = self._np.array(img, dtype=self._np.uint8)
            gray = self._np.mean(arr[:, :, :3], axis=2).astype(self._np.float32)
            variance = float(self._np.var(gray))
            active, mean_delta, changed_fraction = self._frame_change(gray)

        foreground_app = None
        foreground_exe = None
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            hwnd = user32.GetForegroundWindow()

            # 窗口标题
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                foreground_app = buf.value

            # 进程名（替代关键词匹配，零额外依赖）
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                PROCESS_QUERY_LIMITED_INFO = 0x1000
                h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFO, False, pid.value)
                if h_process:
                    exe_buf = ctypes.create_unicode_buffer(260)
                    exe_size = wintypes.DWORD(260)
                    # QueryFullProcessImageNameW → 获取完整路径，取文件名
                    if kernel32.QueryFullProcessImageNameW(h_process, 0, exe_buf, ctypes.byref(exe_size)):
                        import os
                        foreground_exe = os.path.basename(exe_buf.value)
                    kernel32.CloseHandle(h_process)
        except Exception:
            pass

        return {
            "screen_active": active,
            "variance": round(variance, 1),
            "frame_mean_delta": round(mean_delta, 2) if mean_delta is not None else None,
            "frame_changed_fraction": changed_fraction,
            "foreground_app": foreground_app,
            "foreground_exe": foreground_exe,
        }

    @property
    def connection_state(self) -> str:
        return "connected" if self._mss_available else "unavailable"
