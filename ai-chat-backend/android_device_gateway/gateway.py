"""A bounded Android device gateway shared by local domain agents.

This is intentionally a *primitive* API.  It does not expose ``shell`` or
accept an arbitrary command string.  The production implementation is still
opt-in: callers must construct :class:`AdbDeviceGateway` explicitly.  Tests
and offline development use :class:`InMemoryDeviceGateway`, which cannot
touch a phone.

The lease is process-wide and covers an entire domain operation.  Holding it
across a checkout flow prevents XHS and Meituan from interleaving taps
on the same screen.  A submitting operation is persisted by the domain state
machine before the first submit primitive; a crash therefore leads to
reconciliation rather than a second tap.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import inspect
import logging
import os
import re
import subprocess
import threading
import time
import tempfile
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
logger = logging.getLogger(__name__)
XHS_PACKAGE = 'com.xingin.xhs'
MEITUAN_PACKAGE = 'com.sankuai.meituan'
ALLOWED_PACKAGES = frozenset({XHS_PACKAGE, MEITUAN_PACKAGE})
UI_DUMP_PATH = '/sdcard/mirrow_device_ui.xml'
MAX_INPUT_TEXT = 500
MAX_COORDINATE = 4320

class DeviceBusyError(RuntimeError):
    """The dedicated phone is currently owned by another operation."""

class LeaseUnavailableError(DeviceBusyError):
    """Compatibility alias with a more descriptive name."""

@dataclass(frozen=True)
class AdbCommandResult:
    returncode: int = 0
    stdout: str | bytes = ''
    stderr: str | bytes = ''

@dataclass(frozen=True)
class DeviceLease:
    """An opaque ownership token returned by :class:`DeviceLeaseManager`."""
    owner: str
    lease_id: str
    acquired_at: float
    _manager: 'DeviceLeaseManager' = field(repr=False, compare=False)

    def release(self) -> None:
        self._manager.release(self)

    def __enter__(self) -> 'DeviceLease':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

class DeviceLeaseManager:
    """Cross-process, non-reentrant lease for the dedicated Android phone.

    A small OS file lock is the production authority.  The lock is released by
    the OS when a process exits unexpectedly, while the in-memory lock only
    prevents same-process re-entry.  ``memory_only`` exists solely for tests.
    """

    def __init__(self, *, lock_path: str | os.PathLike[str] | None=None, device_key: str='dedicated_android_phone', memory_only: bool=False) -> None:
        self._lock = threading.Lock()
        self._active: DeviceLease | None = None
        self._counter = 0
        self._memory_only = bool(memory_only)
        safe_key = hashlib.sha256(str(device_key or 'dedicated_android_phone').encode('utf-8', 'ignore')).hexdigest()[:20]
        configured_dir = os.getenv('MIRROW_ANDROID_LEASE_DIR', '')
        self._lock_path = Path(lock_path) if lock_path else Path(configured_dir or tempfile.gettempdir()) / f'mirrow_android_{safe_key}.lock'
        self._file_handle: Any = None

    @property
    def busy(self) -> bool:
        return self._active is not None

    def acquire(self, owner: str, *, timeout: float=0.0) -> DeviceLease:
        owner = str(owner or '').strip()
        if not owner or len(owner) > 100:
            raise ValueError('owner_required')
        timeout = max(0.0, float(timeout))
        deadline = time.monotonic() + timeout
        acquired = self._lock.acquire(False)
        if not acquired:
            raise LeaseUnavailableError('android_device_busy')
        try:
            if self._active is not None:
                raise LeaseUnavailableError('android_device_busy')
            lock_handle = None
            if not self._memory_only:
                self._lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_handle = open(self._lock_path, 'a+b')
                while True:
                    try:
                        lock_handle.seek(0)
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except (BlockingIOError, OSError):
                        if time.monotonic() >= deadline:
                            lock_handle.close()
                            raise LeaseUnavailableError('android_device_busy')
                        time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
                self._file_handle = lock_handle
            self._counter += 1
            lease = DeviceLease(owner, f'lease-{self._counter}', time.monotonic(), self)
            self._active = lease
            return lease
        except Exception:
            if self._file_handle is not None:
                try:
                    self._unlock_file(self._file_handle)
                finally:
                    self._file_handle = None
            self._lock.release()
            raise

    async def acquire_async(self, owner: str, *, timeout: float=0.0) -> DeviceLease:
        if timeout <= 0:
            return self.acquire(owner, timeout=0)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.acquire(owner, timeout=timeout))

    def release(self, lease: DeviceLease) -> None:
        if self._active is not lease:
            return
        self._active = None
        handle = self._file_handle
        self._file_handle = None
        if handle is not None:
            self._unlock_file(handle)
        self._lock.release()

    @staticmethod
    def _unlock_file(handle: Any) -> None:
        try:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        try:
            handle.close()
        except (OSError, ValueError):
            pass
_GLOBAL_LEASE_MANAGER = DeviceLeaseManager()

class DeviceGateway:
    """Protocol-like base class for safe device primitives."""
    lease_manager: DeviceLeaseManager

    def __init__(self, *, lease_manager: DeviceLeaseManager | None=None) -> None:
        self.lease_manager = lease_manager or _GLOBAL_LEASE_MANAGER

    @contextmanager
    def lease(self, owner: str, *, timeout: float=0.0) -> Iterator[DeviceLease]:
        lease = self.lease_manager.acquire(owner, timeout=timeout)
        try:
            yield lease
        finally:
            lease.release()

    async def lease_async(self, owner: str, *, timeout: float=0.0) -> DeviceLease:
        return await self.lease_manager.acquire_async(owner, timeout=timeout)

    async def get_state(self) -> str:
        raise NotImplementedError

    async def battery(self) -> Mapping[str, Any]:
        raise NotImplementedError

    async def ui_dump(self) -> str:
        raise NotImplementedError

    async def screenshot(self) -> bytes:
        raise NotImplementedError

    async def wake(self) -> bool:
        raise NotImplementedError

    async def tap(self, x: int, y: int) -> bool:
        raise NotImplementedError

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int=280) -> bool:
        raise NotImplementedError

    async def input_text(self, text: str) -> bool:
        raise NotImplementedError

    async def launch(self, package: str, *, component: str='', uri: str='') -> bool:
        raise NotImplementedError

    async def reconcile_orders(self, domain: str, task_id: str) -> Mapping[str, Any]:
        """Read-only order lookup used after a submitting crash."""
        raise NotImplementedError

def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return str(value or '')

def _safe_package(package: str) -> bool:
    return str(package or '') in ALLOWED_PACKAGES

def _safe_uri(uri: str) -> bool:
    value = str(uri or '')
    return bool(value) and len(value) <= 300 and (not re.search('[\\x00-\\x1f\\x7f]', value))

class AdbDeviceGateway(DeviceGateway):
    """Fixed-argv ADB implementation.

    ``serial`` is retained solely as an internal route.  It is never emitted
    in a result or log message.  ``command_runner`` is injectable for tests;
    production uses ``subprocess.run(..., shell=False)``.
    """

    def __init__(self, *, serial: str | None=None, command_runner: Callable[..., Any] | None=None, command_timeout_seconds: float=8.0, allowed_packages: Iterable[str]=ALLOWED_PACKAGES, lease_manager: DeviceLeaseManager | None=None) -> None:
        super().__init__(lease_manager=lease_manager)
        configured = serial if serial is not None else os.getenv('MIRROW_ANDROID_DEVICE_SERIAL', '')
        self._serial = str(configured or '').strip()
        self._runner = command_runner or self._default_runner
        self.command_timeout_seconds = max(1.0, float(command_timeout_seconds))
        self.allowed_packages = frozenset((str(p) for p in allowed_packages)) & ALLOWED_PACKAGES

    @staticmethod
    def _default_runner(argv: Sequence[str], timeout_seconds: float) -> AdbCommandResult:
        completed = subprocess.run(list(argv), capture_output=True, check=False, timeout=max(1.0, float(timeout_seconds)), shell=False)
        return AdbCommandResult(completed.returncode, completed.stdout or b'', completed.stderr or b'')

    async def _invoke(self, argv: Sequence[str]) -> AdbCommandResult | None:
        if not self._allowed_argv(argv):
            logger.warning('blocked non-whitelisted Android operation')
            return None
        try:
            try:
                if self._runner is self._default_runner:
                    pending = asyncio.create_task(asyncio.to_thread(self._runner, argv, self.command_timeout_seconds))
                    try:
                        result = await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        while not pending.done():
                            try:
                                await asyncio.shield(pending)
                            except asyncio.CancelledError:
                                continue
                            except Exception:
                                break
                        if pending.done() and (not pending.cancelled()):
                            pending.exception()
                        raise
                else:
                    result = self._runner(argv, self.command_timeout_seconds)
            except TypeError:
                result = self._runner(argv)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, AdbCommandResult):
                return result
            return AdbCommandResult(int(getattr(result, 'returncode', 0) or 0), getattr(result, 'stdout', '') or '', getattr(result, 'stderr', '') or '')
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            logger.debug('Android operation unavailable')
            return None
        except Exception as exc:
            logger.debug('Android operation failed: %s', type(exc).__name__)
            return None

    def _device(self, *args: str) -> tuple[str, ...]:
        if not self._serial:
            return ('adb', *args)
        return ('adb', '-s', self._serial, *args)

    @classmethod
    def _allowed_argv(cls, argv: Sequence[str]) -> bool:
        parts = tuple((str(x) for x in argv))
        if parts == ('adb', 'devices'):
            return True
        if len(parts) < 2 or parts[0] != 'adb':
            return False
        offset = 1
        if len(parts) >= 3 and parts[1] == '-s':
            if not parts[2] or len(parts) < 4:
                return False
            offset = 3
        args = parts[offset:]
        if args in {('get-state',), ('shell', 'dumpsys', 'battery'), ('shell', 'dumpsys', 'window', 'policy'), ('shell', 'uiautomator', 'dump', UI_DUMP_PATH), ('shell', 'cat', UI_DUMP_PATH), ('exec-out', 'screencap', '-p'), ('shell', 'input', 'keyevent', 'KEYCODE_WAKEUP')}:
            return True
        if len(args) == 8 and args[:3] == ('shell', 'input', 'swipe'):
            try:
                values = [int(v) for v in args[3:]]
            except ValueError:
                return False
            return all((0 <= v <= MAX_COORDINATE for v in values[:4])) and 0 < values[4] <= 5000
        if len(args) == 5 and args[:3] == ('shell', 'input', 'tap'):
            try:
                x, y = (int(args[3]), int(args[4]))
            except ValueError:
                return False
            return 0 <= x <= MAX_COORDINATE and 0 <= y <= MAX_COORDINATE
        if len(args) == 4 and args[:3] == ('shell', 'input', 'text'):
            value = args[3]
            return 0 < len(value) <= MAX_INPUT_TEXT and (not re.search('[\\x00-\\x1f\\x7f]', value))
        if len(args) >= 9 and args[:4] == ('shell', 'am', 'start', '-W'):
            return args[4] in {'-a', '-n'} and all((not re.search('[\\x00-\\x1f\\x7f]', p) for p in args))
        return False

    async def _selected_serial(self) -> str | None:
        if self._serial:
            return self._serial
        result = await self._invoke(('adb', 'devices'))
        if result is None or result.returncode != 0:
            return None
        ready: list[str] = []
        for line in _as_text(result.stdout).splitlines():
            fields = line.strip().split()
            if len(fields) >= 2 and fields[1].lower() == 'device':
                ready.append(fields[0])
        if not ready and self._runner is self._default_runner:
            from .discovery import reconnect_wireless
            if await reconnect_wireless():
                result = await self._invoke(('adb', 'devices'))
                for line in _as_text(result.stdout if result else '').splitlines():
                    fields = line.strip().split()
                    if len(fields) >= 2 and fields[1] == 'device':
                        ready.append(fields[0])
        return ready[0] if len(ready) == 1 else None

    async def _device_invoke(self, *args: str) -> AdbCommandResult | None:
        serial = await self._selected_serial()
        if not serial:
            return None
        return await self._invoke(('adb', '-s', serial, *args))

    async def get_state(self) -> str:
        result = await self._device_invoke('get-state')
        return _as_text(result.stdout).strip().splitlines()[0].lower() if result and result.returncode == 0 and _as_text(result.stdout).strip() else ''

    async def battery(self) -> Mapping[str, Any]:
        result = await self._device_invoke('shell', 'dumpsys', 'battery')
        raw = _as_text(result.stdout) if result and result.returncode == 0 else ''

        def first(pattern: str) -> int | None:
            match = re.search(pattern, raw, flags=re.I | re.M)
            try:
                return int(match.group(1)) if match else None
            except (TypeError, ValueError):
                return None
        level, status, plugged = (first('^\\s*level\\s*:\\s*(\\d+)'), first('^\\s*status\\s*:\\s*(\\d+)'), first('^\\s*plugged\\s*:\\s*(\\d+)'))
        charging = bool(plugged and plugged > 0) or status in {2, 5} or bool(re.search('powered\\s*:\\s*true', raw, re.I))
        return {'level': level, 'status': status, 'plugged': plugged, 'charging': charging}

    async def ui_dump(self) -> str:
        dumped = await self._device_invoke('shell', 'uiautomator', 'dump', UI_DUMP_PATH)
        if dumped is None or dumped.returncode != 0:
            return ''
        content = await self._device_invoke('shell', 'cat', UI_DUMP_PATH)
        return _as_text(content.stdout) if content and content.returncode == 0 else ''

    async def screenshot(self) -> bytes:
        result = await self._device_invoke('exec-out', 'screencap', '-p')
        if result is None or result.returncode != 0:
            return b''
        return result.stdout if isinstance(result.stdout, bytes) else _as_text(result.stdout).encode()

    async def wake(self) -> bool:
        result = await self._device_invoke('shell', 'input', 'keyevent', 'KEYCODE_WAKEUP')
        return bool(result and result.returncode == 0)

    async def tap(self, x: int, y: int) -> bool:
        result = await self._device_invoke('shell', 'input', 'tap', str(int(x)), str(int(y)))
        return bool(result and result.returncode == 0)

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int=280) -> bool:
        result = await self._device_invoke('shell', 'input', 'swipe', str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration_ms)))
        return bool(result and result.returncode == 0)

    async def input_text(self, text: str) -> bool:
        value = str(text or '')[:MAX_INPUT_TEXT]
        if not value or re.search('[\\x00-\\x1f\\x7f]', value):
            return False
        encoded = value.replace('%', '%25').replace(' ', '%s')
        result = await self._device_invoke('shell', 'input', 'text', encoded)
        return bool(result and result.returncode == 0)

    async def launch(self, package: str, *, component: str='', uri: str='') -> bool:
        package = str(package or '')
        if package not in self.allowed_packages or not _safe_package(package):
            return False
        if component:
            if not re.fullmatch('[A-Za-z0-9_.$]+(?:/[A-Za-z0-9_.$]+)?', component):
                return False
            args = ('shell', 'am', 'start', '-W', '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', '-n', f"{package}/{component.lstrip('/').split('/', 1)[-1]}")
        elif uri and _safe_uri(uri):
            args = ('shell', 'am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d', uri, '-p', package)
        else:
            return False
        result = await self._device_invoke(*args)
        return bool(result and result.returncode == 0)

    async def reconcile_orders(self, domain: str, task_id: str) -> Mapping[str, Any]:
        return {'status': 'reconcile_unavailable', 'domain': str(domain or '')[:30], 'task_id': str(task_id or '')[:80]}

class InMemoryDeviceGateway(DeviceGateway):
    """Offline fake device used by deterministic tests.

    Calls are recorded as operation names only; payloads are available through
    ``calls`` for assertions, but no serial, address or credential is ever
    generated by this fake.
    """

    def __init__(self, *, state: str='device', battery_data: Mapping[str, Any] | None=None, ui: str='', screenshot_bytes: bytes=b'', submit_results: Mapping[str, Any] | None=None, lease_manager: DeviceLeaseManager | None=None) -> None:
        super().__init__(lease_manager=lease_manager)
        self.state = state
        self.battery_data = dict(battery_data or {'level': 100, 'charging': True})
        self.ui = ui
        self.screenshot_bytes = screenshot_bytes
        self.submit_results = dict(submit_results or {})
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    async def get_state(self) -> str:
        self._record('get_state')
        return self.state

    async def battery(self) -> Mapping[str, Any]:
        self._record('battery')
        return dict(self.battery_data)

    async def ui_dump(self) -> str:
        self._record('ui_dump')
        return self.ui

    async def screenshot(self) -> bytes:
        self._record('screenshot')
        return bytes(self.screenshot_bytes)

    async def wake(self) -> bool:
        self._record('wake')
        return True

    async def tap(self, x: int, y: int) -> bool:
        self._record('tap', int(x), int(y))
        return True

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int=280) -> bool:
        self._record('swipe', int(x1), int(y1), int(x2), int(y2), int(duration_ms))
        return True

    async def input_text(self, text: str) -> bool:
        self._record('input_text', str(text))
        return True

    async def launch(self, package: str, *, component: str='', uri: str='') -> bool:
        self._record('launch', str(package), str(component), str(uri))
        return True

    async def reconcile_orders(self, domain: str, task_id: str) -> Mapping[str, Any]:
        self._record('reconcile_orders', str(domain), str(task_id))
        return dict(self.submit_results.get('reconcile', {'status': 'not_found'}))
__all__ = ['AdbCommandResult', 'AdbDeviceGateway', 'DeviceBusyError', 'DeviceGateway', 'DeviceLease', 'DeviceLeaseManager', 'InMemoryDeviceGateway', 'LeaseUnavailableError']
