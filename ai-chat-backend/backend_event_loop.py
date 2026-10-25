"""Windows accept recovery without sacrificing asyncio subprocess support.

CPython #93821: ERROR_NETNAME_DELETED during AcceptEx closes the listener.
Keep that one transient completion local to the unaccepted connection.
"""
import asyncio
import logging
import socket
import struct
import sys

if sys.platform == 'win32':
    import _overlapped
    from asyncio.windows_events import IocpProactor

    class BackendProactor(IocpProactor):
        def accept(self, listener):
            return asyncio.ensure_future(self._accept_retry(listener), loop=self._loop)

        def _accept_once(self, listener):
            # Same AcceptEx setup as CPython; one task owns completion and cleanup.
            self._register_with_iocp(listener)
            conn = self._get_accept_socket(listener.family)
            try:
                ov = _overlapped.Overlapped(0)
                ov.AcceptEx(listener.fileno(), conn.fileno())

                def finish_accept(trans, key, completed):
                    completed.getresult()
                    conn.setsockopt(socket.SOL_SOCKET, _overlapped.SO_UPDATE_ACCEPT_CONTEXT,
                                    struct.pack('@P', listener.fileno()))
                    conn.settimeout(listener.gettimeout())
                    return conn, conn.getpeername()

                return self._register(ov, listener, finish_accept), conn
            except BaseException:
                conn.close()
                raise

        async def _accept_retry(self, listener):
            while True:
                conn = None
                try:
                    pending, conn = self._accept_once(listener)
                    return await pending
                except OSError as exc:
                    if conn is not None:
                        conn.close()
                    if exc.winerror != 64 or listener.fileno() == -1:
                        raise
                    logging.getLogger(__name__).warning(
                        'AcceptEx WinError 64; listener retained, retrying accept')
                    await asyncio.sleep(0.05)
                except BaseException:
                    if conn is not None:
                        conn.close()
                    raise


def create_loop():
    if sys.platform == 'win32':
        loop = asyncio.ProactorEventLoop(proactor=BackendProactor())
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop
