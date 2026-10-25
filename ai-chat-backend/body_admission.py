"""Process-local body work admission. No persisted lock survives a restart."""
import asyncio
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import uuid

_owner = None
_phase = ''
_direction = ''
_normal = set()
_work = ContextVar('body_normal_work', default=())
_encounter = ContextVar('body_focus_owner', default=None)
BUSY_MESSAGE = 'AI 正在完成另一个前台活动，请稍后重试；输入内容可以先保留。'


def busy_message():
    labels = {'xhs': '查看小红书互动', 'social': '查看共域互动', 'workshop': '处理工作坊事项'}
    return f"AI 正在{labels.get(_direction, '完成前台活动')}，请稍后重试。"


def busy():
    return _owner is not None


def current_owner():
    return _owner


def requested_owner():
    return _encounter.get()


def status():
    label = {'xhs': '查看小红书互动中', 'social': '查看共域互动中', 'workshop': '工作坊活动中'}.get(_direction, '前台活动中') if busy() else ''
    return {'busy': busy(), 'phase': _phase, 'direction': _direction, 'label': label,
            'ordinary_work_count': len(_normal),
            'message': busy_message() if busy() else ''}


def enter_work():
    """Atomic admission before any message persistence; nested work can finish."""
    inherited = _work.get()
    if _owner is not None and _encounter.get() == _owner:
        return None
    if busy() and not any(item in _normal for item in inherited):
        raise RuntimeError(busy_message())
    identifier = uuid.uuid4().hex
    _normal.add(identifier)
    return identifier, _work.set((*inherited, identifier))


def leave_work(lease):
    if lease is not None:
        identifier, token = lease
        _normal.discard(identifier)
        _work.reset(token)


def reserve(*, require_idle=False, direction='foreground'):
    global _owner, _phase, _direction
    if busy() or (require_idle and _normal):
        raise RuntimeError(busy_message())
    _owner = uuid.uuid4().hex
    _phase = 'active' if require_idle else 'preparing'
    _direction = direction
    return _owner


async def drain(identifier):
    global _phase
    # A wander task may itself choose to visit; it must not wait on itself.
    async with asyncio.timeout(120):
        while _normal - set(_work.get()):
            if _owner != identifier:
                raise RuntimeError('前台活动等待已取消')
            await asyncio.sleep(0.05)
    if _owner == identifier:
        _phase = 'active'


def release(identifier):
    global _owner, _phase, _direction
    if identifier is not None and _owner == identifier:
        _owner = None
        _phase = _direction = ''


@contextmanager
def owned(identifier):
    token = _encounter.set(identifier)
    try:
        yield
    finally:
        _encounter.reset(token)


def ordinary(fn):
    """Wait before reading/claiming due work, rather than dropping reminders."""
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        while True:
            try:
                lease = enter_work()
                break
            except RuntimeError:
                await asyncio.sleep(0.1)
        try:
            return await fn(*args, **kwargs)
        finally:
            leave_work(lease)
    return wrapped


def optional_observation(fn):
    """Transient observations are sampled again later, not queued as stale speech."""
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        try:
            lease = enter_work()
        except RuntimeError:
            return None
        try:
            return await fn(*args, **kwargs)
        finally:
            leave_work(lease)
    return wrapped


class PrivateChatAdmission:
    """HTTP private chat admission includes persistence and response completion."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') != '/chat':
            return await self.app(scope, receive, send)
        from wander_manager import game_chat_gate
        try:
            game_lease = game_chat_gate.enter_chat()
        except RuntimeError:
            from starlette.responses import JSONResponse
            return await JSONResponse({'detail': game_chat_gate.BUSY_MESSAGE}, status_code=409)(scope, receive, send)
        try:
            lease = enter_work()
        except RuntimeError:
            game_chat_gate.leave_chat(game_lease)
            from starlette.responses import JSONResponse
            return await JSONResponse({'detail': busy_message()}, status_code=409)(scope, receive, send)
        try:
            return await self.app(scope, receive, send)
        finally:
            leave_work(lease)
            game_chat_gate.leave_chat(game_lease)
