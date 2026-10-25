"""Read-only probe of Codex's local thread-writer ownership (no session bodies).

Codex rollout/writer_lock.rs serializes creation/removal using .coordination.lock.
Hold a nonblocking shared coordination lock while probing; never create/delete
lock files or keep ownership between polls. None means the source is unavailable.
"""
from contextlib import contextmanager
import os
from uuid import UUID


@contextmanager
def shared_probe(file):
    if os.name == 'nt':
        import ctypes
        import msvcrt

        class Overlapped(ctypes.Structure):
            _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                        ('Offset', ctypes.c_ulong), ('OffsetHigh', ctypes.c_ulong),
                        ('hEvent', ctypes.c_void_p)]

        api = ctypes.WinDLL('kernel32', use_last_error=True)
        args = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                ctypes.c_ulong, ctypes.POINTER(Overlapped)]
        api.LockFileEx.argtypes = args
        api.UnlockFileEx.argtypes = args[:1] + args[2:]
        handle = msvcrt.get_osfhandle(file.fileno())
        overlap = Overlapped()
        acquired = bool(api.LockFileEx(handle, 1, 0, 1, 0, ctypes.byref(overlap)))
        if not acquired and ctypes.get_last_error() != 33:  # ERROR_LOCK_VIOLATION
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield acquired
        finally:
            if acquired:
                api.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap))
    else:
        import fcntl
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        try:
            yield acquired
        finally:
            if acquired:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def writer_owned(home, thread_id):
    """True: live owner; False: unloaded/orphaned; None: cannot verify."""
    try:
        if str(UUID(thread_id)) != thread_id:
            return None
        directory = home / 'thread-writer-locks'
        if directory.resolve().parent != home:
            return None
        with (directory / '.coordination.lock').open('rb') as coordination:
            with shared_probe(coordination) as stable:
                if not stable:
                    return None
                try:
                    with (directory / (thread_id + '.lock')).open('rb') as writer:
                        with shared_probe(writer) as available:
                            return not available
                except FileNotFoundError:
                    return False
    except (OSError, ValueError, TypeError):
        return None
