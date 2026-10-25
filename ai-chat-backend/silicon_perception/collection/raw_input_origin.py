"""Windows Raw Input 来源识别。

``GetLastInputInfo`` 只回答“系统最近收到过输入”，并不说明输入来自本机
还是远程控制端。这个模块把键盘/鼠标 Raw Input 注册到一个隐藏消息窗口，
根据 ``RAWINPUTHEADER.hDevice`` 对应的设备接口路径识别来源：

* ``GVINPUT``：UU/GameViewer 虚拟 HID，归为远程输入；
* 带真实 ``VID_``/``PID_`` 的 HID 设备：归为本地物理输入；
* RDP 路径：归为远程输入；
* 其他/无法解析：未知，不作为本地输入证据。

Raw Input 线程只负责收集来源和时间，行为状态由上层按“最近一次来源”解释。
本模块不记录键值、鼠标坐标、设备序列号或路径到数据库，避免把设备细节带入
上下文和持久化数据。
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class InputOrigin(str, Enum):
    """Raw Input 设备来源。"""

    PHYSICAL = "physical"
    REMOTE = "remote"
    UNKNOWN = "unknown"


def classify_device_path(device_path: Optional[str]) -> InputOrigin:
    """将 Windows Raw Input 设备接口路径归类。

    分类顺序刻意先判断 UU，再判断 HID。UU 的设备路径本身通常也包含 HID，
    若先按 ``HID`` 判断会把 GVINPUT 错当成本地物理输入。
    """

    if not device_path:
        return InputOrigin.UNKNOWN

    path = str(device_path).upper()
    if "GVINPUT" in path:
        return InputOrigin.REMOTE

    # RDP 的键鼠设备名称在不同 Windows 版本/会话中略有差异，保守覆盖常见
    # 标记。命中 RDP 只作为远程输入，不依赖远程控制软件进程是否存在。
    if any(marker in path for marker in (
        "RDP",
        "TERMSRV",
        "TSMM",
        "REMOTEINPUT",
    )):
        return InputOrigin.REMOTE

    # Raw Input 的真实本机键鼠通常是 HID\VID_xxxx&PID_xxxx；只有 HID 但没有
    # VID/PID 的路径可能是虚拟/桥接设备，不能当作 T1 本地输入。
    if ("HID#VID_" in path or "HID\\VID_" in path) and "&PID_" in path:
        return InputOrigin.PHYSICAL

    return InputOrigin.UNKNOWN


@dataclass(frozen=True)
class InputOriginSnapshot:
    """当前输入来源状态（只含时间间隔，不含原始设备细节）。"""

    physical_input_idle: Optional[float]
    remote_input_idle: Optional[float]
    available: bool
    startup_complete: bool
    last_origin: Optional[InputOrigin]


# Windows Raw Input constants.
_WM_INPUT = 0x00FF
_WM_QUIT = 0x0012
_WM_TIMER = 0x0113
_RID_INPUT = 0x10000003
_RIDI_DEVICENAME = 0x20000007
_RIM_TYPEMOUSE = 0
_RIM_TYPEKEYBOARD = 1
_RIDEV_INPUTSINK = 0x00000100
_HWND_MESSAGE = -3


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class _WNDCLASSW(ctypes.Structure):
    # lpfnWndProc is assigned the matching WINFUNCTYPE below. Keeping the field
    # typed as c_void_p also works on both 32/64-bit Python and avoids truncating
    # the callback pointer during structure construction.
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        # ctypes.wintypes does not expose HCURSOR on every Python build;
        # it is a pointer-sized handle just like HICON/HBRUSH.
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# WNDPROC callback reference is held on every tracker instance as well as in
# the closure; ctypes must not garbage-collect it while the window exists.
_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


def _windows_available() -> bool:
    return sys.platform == "win32" and hasattr(ctypes, "windll")


class RawInputOriginTracker:
    """后台收集 Raw Input 来源并提供物理/远程两个独立 idle。

    一个进程只创建一个 tracker（见 :func:`get_input_origin_tracker`）。Raw Input
    每类设备只能在本进程注册到一个目标窗口，故注册键盘和鼠标时统一指向本类
    创建的同一个消息窗口。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._started = False
        self._startup_complete = False
        self._available = False
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._hwnd = None
        self._class_name: Optional[str] = None
        self._last_physical_at: Optional[float] = None
        self._last_remote_at: Optional[float] = None
        self._last_origin: Optional[InputOrigin] = None
        self._user32 = None
        self._kernel32 = None
        self._wnd_proc_ref: Optional[Any] = None
        self._wnd_class_ref: Optional[_WNDCLASSW] = None
        self._last_packet_at: Optional[float] = None
        self._last_pump_at: Optional[float] = None
        self._packet_count = 0
        self._unknown_count = 0
        self._parse_errors = 0
        self._last_error = ""

    @property
    def available(self) -> bool:
        return self._available

    @property
    def startup_complete(self) -> bool:
        return self._startup_complete

    def start(self, wait_seconds: float = 1.0) -> bool:
        """启动 Raw Input 消息线程。

        ``wait_seconds`` 只等待注册结果，不等待第一笔输入；启动后没有物理输入
        时 idle 保持 ``None``，不会回退到无法区分来源的 ``GetLastInputInfo``。
        """

        if self._started:
            if wait_seconds > 0 and not self._startup_complete:
                self._wait_for_startup(wait_seconds)
            return self._available

        with self._lock:
            if self._started:
                return self._available
            self._started = True
            if not _windows_available():
                self._startup_complete = True
                return False
            self._thread = threading.Thread(
                target=self._thread_main,
                name="raw-input-origin",
                daemon=True,
            )
            self._thread.start()

        if wait_seconds > 0:
            self._wait_for_startup(wait_seconds)
        return self._available

    def _wait_for_startup(self, wait_seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while not self._startup_complete and time.monotonic() < deadline:
            time.sleep(0.01)

    def stop(self, join_seconds: float = 1.0) -> None:
        """停止消息线程（服务关闭和测试使用）。"""

        thread = self._thread
        if not thread:
            return
        if self._thread_id and self._user32:
            try:
                self._user32.PostThreadMessageW(
                    wintypes.DWORD(self._thread_id), _WM_QUIT, 0, 0)
            except Exception:
                logger.debug("Raw Input 消息线程退出通知失败", exc_info=True)
        if thread is not threading.current_thread():
            thread.join(max(0.0, join_seconds))
        self._thread = None
        self._thread_id = None
        self._hwnd = None
        self._available = False
        self._startup_complete = False
        self._started = False

    def snapshot(self, now: Optional[float] = None) -> InputOriginSnapshot:
        """返回物理输入和远程输入各自距最近事件的秒数。"""

        current = time.monotonic() if now is None else now
        with self._lock:
            physical = self._last_physical_at
            remote = self._last_remote_at
            last_origin = self._last_origin
            available = self._available
            startup_complete = self._startup_complete
        return InputOriginSnapshot(
            physical_input_idle=(max(0.0, current - physical) if physical is not None else None),
            remote_input_idle=(max(0.0, current - remote) if remote is not None else None),
            available=available,
            startup_complete=startup_complete,
            last_origin=last_origin,
        )

    def record_origin(self, origin: InputOrigin, when: Optional[float] = None) -> None:
        """记录一次已分类的输入；公开给测试和平台适配层使用。"""

        if not isinstance(origin, InputOrigin):
            origin = InputOrigin(str(origin))
        event_at = time.monotonic() if when is None else when
        with self._lock:
            self._last_origin = origin
            if origin is InputOrigin.PHYSICAL:
                self._last_physical_at = event_at
            elif origin is InputOrigin.REMOTE:
                self._last_remote_at = event_at

    def diagnostics(self, now: Optional[float] = None) -> dict:
        """Counts/ages only: never expose key values, device paths or coordinates."""
        current = time.monotonic() if now is None else now
        with self._lock:
            age = lambda at: round(max(0.0, current - at), 1) if at is not None else None
            pump_age = age(self._last_pump_at)
            alive = bool(self._thread and self._thread.is_alive())
            return {
                "available": self._available,
                "thread_alive": alive,
                "pump_age_seconds": pump_age,
                "packet_age_seconds": age(self._last_packet_at),
                "physical_idle_seconds": age(self._last_physical_at),
                "remote_idle_seconds": age(self._last_remote_at),
                "packets": self._packet_count,
                "unknown_packets": self._unknown_count,
                "parse_errors": self._parse_errors,
                "last_error": self._last_error,
                "health": ("not_started" if not self._started else "unavailable"
                           if not self._available or not alive else "pump_unresponsive"
                           if pump_age is None or pump_age > 10 else "no_packets"
                           if not self._packet_count else "receiving"),
            }

    # ── Windows Raw Input implementation ──────────────────────────────

    def _thread_main(self) -> None:
        try:
            self._setup_windows_api()
            self._thread_id = int(self._kernel32.GetCurrentThreadId())
            self._create_message_window()
            if not self._register_raw_input():
                return
            if not self._user32.SetTimer(self._hwnd, 1, 1000, None):
                raise OSError("raw_input_timer_failed")
            self._last_pump_at = time.monotonic()
            self._available = True
            self._startup_complete = True
            logger.info("Raw Input 来源识别已启动（键盘+鼠标）")

            msg = wintypes.MSG()
            while True:
                result = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self._last_error = type(exc).__name__
            logger.warning("Raw Input 来源识别启动失败: %s", exc)
        finally:
            if not self._startup_complete:
                self._startup_complete = True
            self._available = False
            if self._hwnd and self._user32:
                try:
                    self._user32.KillTimer(self._hwnd, 1)
                    self._user32.DestroyWindow(self._hwnd)
                except Exception:
                    pass
            if self._class_name and self._user32 and self._kernel32:
                try:
                    hinstance = self._kernel32.GetModuleHandleW(None)
                    self._user32.UnregisterClassW(self._class_name, hinstance)
                except Exception:
                    pass

    def _setup_windows_api(self) -> None:
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self._kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self._user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        self._user32.RegisterClassW.restype = wintypes.ATOM
        self._user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        self._user32.UnregisterClassW.restype = wintypes.BOOL
        self._user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        self._user32.CreateWindowExW.restype = wintypes.HWND
        self._user32.DestroyWindow.argtypes = [wintypes.HWND]
        self._user32.DestroyWindow.restype = wintypes.BOOL
        self._user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self._user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self._user32.GetMessageW.restype = ctypes.c_int
        self._user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self._user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self._user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._user32.PostThreadMessageW.restype = wintypes.BOOL
        self._user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t,
                                         wintypes.UINT, ctypes.c_void_p]
        self._user32.SetTimer.restype = ctypes.c_size_t
        self._user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        self._user32.KillTimer.restype = wintypes.BOOL

        self._user32.RegisterRawInputDevices.argtypes = [
            ctypes.POINTER(_RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
        self._user32.RegisterRawInputDevices.restype = wintypes.BOOL
        self._user32.GetRawInputData.argtypes = [
            wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
            ctypes.POINTER(wintypes.UINT), wintypes.UINT]
        self._user32.GetRawInputData.restype = wintypes.UINT
        self._user32.GetRawInputDeviceInfoW.argtypes = [
            wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
            ctypes.POINTER(wintypes.UINT)]
        self._user32.GetRawInputDeviceInfoW.restype = ctypes.c_int

    def _create_message_window(self) -> None:
        hinstance = self._kernel32.GetModuleHandleW(None)
        self._class_name = f"MIRROW.RawInput.{os.getpid()}.{id(self)}"

        @_WNDPROC
        def _wnd_proc(hwnd, message, w_param, l_param):
            if message == _WM_TIMER:
                with self._lock:
                    self._last_pump_at = time.monotonic()
                return 0
            if message == _WM_INPUT:
                with self._lock:
                    self._last_packet_at = time.monotonic()
                    self._last_pump_at = self._last_packet_at
                    self._packet_count += 1
                try:
                    self._handle_wm_input(l_param)
                except Exception:
                    with self._lock:
                        self._parse_errors += 1
                    # Raw Input must never tear down the message loop because a
                    # malformed device packet was received.
                    logger.debug("Raw Input 数据解析失败", exc_info=True)
            # DefWindowProc is required for WM_INPUT cleanup after GetRawInputData.
            return self._user32.DefWindowProcW(hwnd, message, w_param, l_param)

        self._wnd_proc_ref = _wnd_proc
        self._wnd_class_ref = _WNDCLASSW(
            style=0,
            lpfnWndProc=ctypes.cast(self._wnd_proc_ref, ctypes.c_void_p),
            cbClsExtra=0,
            cbWndExtra=0,
            hInstance=hinstance,
            hIcon=None,
            hCursor=None,
            hbrBackground=None,
            lpszMenuName=None,
            lpszClassName=self._class_name,
        )
        if not self._user32.RegisterClassW(ctypes.byref(self._wnd_class_ref)):
            raise OSError(f"RegisterClassW failed: {ctypes.get_last_error()}")

        self._hwnd = self._user32.CreateWindowExW(
            0, self._class_name, "MIRROW Raw Input", 0,
            0, 0, 0, 0,
            wintypes.HWND(_HWND_MESSAGE), None, hinstance, None,
        )
        if not self._hwnd:
            raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")

    def _register_raw_input(self) -> bool:
        # One target window per device class, deliberately shared by keyboard+mouse.
        devices = (_RAWINPUTDEVICE * 2)(
            _RAWINPUTDEVICE(0x01, 0x02, _RIDEV_INPUTSINK, self._hwnd),  # mouse
            _RAWINPUTDEVICE(0x01, 0x06, _RIDEV_INPUTSINK, self._hwnd),  # keyboard
        )
        ok = self._user32.RegisterRawInputDevices(
            devices, len(devices), ctypes.sizeof(_RAWINPUTDEVICE))
        if not ok:
            self._last_error = "raw_input_registration_failed"
            logger.warning("RegisterRawInputDevices 失败: %s", ctypes.get_last_error())
        return bool(ok)

    def _handle_wm_input(self, raw_input_handle: int) -> None:
        handle = ctypes.c_void_p(int(raw_input_handle) & ((1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1))
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(_RAWINPUTHEADER)
        result = self._user32.GetRawInputData(
            handle, _RID_INPUT, None, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF or size.value < header_size:
            with self._lock:
                self._parse_errors += 1
            return
        payload = ctypes.create_string_buffer(size.value)
        result = self._user32.GetRawInputData(
            handle, _RID_INPUT, payload, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF or size.value < header_size:
            with self._lock:
                self._parse_errors += 1
            return
        header = _RAWINPUTHEADER.from_buffer_copy(payload.raw[:header_size])
        if header.dwType not in (_RIM_TYPEMOUSE, _RIM_TYPEKEYBOARD):
            return
        device_path = self._get_device_path(header.hDevice)
        origin = classify_device_path(device_path)
        if origin is InputOrigin.UNKNOWN:
            with self._lock:
                self._unknown_count += 1
        self.record_origin(origin)

    def _get_device_path(self, device_handle) -> Optional[str]:
        if not device_handle:
            return None
        size = wintypes.UINT(0)
        result = self._user32.GetRawInputDeviceInfoW(
            device_handle, _RIDI_DEVICENAME, None, ctypes.byref(size))
        if result == -1 or size.value <= 0:
            return None
        # RIDI_DEVICENAME size is in WCHARs and can omit the terminating NUL.
        buf = ctypes.create_unicode_buffer(size.value + 1)
        result = self._user32.GetRawInputDeviceInfoW(
            device_handle, _RIDI_DEVICENAME, buf, ctypes.byref(size))
        if result == -1:
            return None
        return buf.value


_tracker_lock = threading.Lock()
_tracker: Optional[RawInputOriginTracker] = None


def get_input_origin_tracker() -> RawInputOriginTracker:
    """返回进程级 Raw Input 来源识别器（惰性创建，不自动启动）。"""

    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = RawInputOriginTracker()
    return _tracker


def reset_input_origin_tracker() -> None:
    """测试/服务重启使用的安全清理入口。"""

    global _tracker
    with _tracker_lock:
        tracker = _tracker
        _tracker = None
    if tracker:
        tracker.stop()


__all__ = [
    "InputOrigin",
    "InputOriginSnapshot",
    "RawInputOriginTracker",
    "classify_device_path",
    "get_input_origin_tracker",
    "reset_input_origin_tracker",
]
