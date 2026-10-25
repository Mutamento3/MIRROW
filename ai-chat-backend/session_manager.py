"""
前端会话管理器
负责存储和管理前端聊天会话数据，实现跨端口同步
"""
import os
import json
import hashlib
import uuid
import asyncio
import logging
import aiofiles
from datetime import datetime
from time_utils import now_iso
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from tool_call_utils import strip_voice_base64
from session_portals import is_visible_session, is_chat_session

logger = logging.getLogger(__name__)


def _msg_sort_key(msg: dict) -> int:
    """按 timestamp 排序的 key 函数，处理 Z/+00:00/naive 格式，无 timestamp 排最后"""
    ts = msg.get("timestamp", "") if isinstance(msg, dict) else (getattr(msg, 'timestamp', '') if hasattr(msg, 'timestamp') else '')
    if not ts:
        return 2**63 - 1  # max int → sort last
    s = ts.strip()
    if s.endswith('Z'): s = s[:-1] + '+00:00'
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000000)
    except Exception:
        return 2**63 - 1


class ThinkingStep(BaseModel):
    """思考步骤模型"""
    content: str
    timestamp: str


class ToolCallRecord(BaseModel):
    """工具调用记录模型"""
    tool: str
    description: Optional[str] = None
    parameters: Dict[str, Any] = {}
    result: Optional[Any] = None
    extra_data: Optional[Dict[str, Any]] = None
    success: Optional[bool] = None
    delivery: str = ""
    status: str = ""
    error: Optional[str] = None


class Message(BaseModel):
    """前端消息模型"""
    id: str
    role: str  # user / assistant / system / divider / legacy notification / event
    content: str
    timestamp: str  # ISO格式
    thinkingChain: Optional[List[ThinkingStep]] = None
    toolCalls: Optional[List[ToolCallRecord]] = None
    isWander: Optional[bool] = None
    isSentinel: Optional[bool] = None
    isReminder: Optional[bool] = None
    isMusicCochlea: Optional[bool] = None
    reasoning_content: Optional[str] = None  # DeepSeek V4 thinking模式原始思维链
    modelSource: Optional[Dict[str, Any]] = None  # UI only; never projected into model context
    eventType: Optional[str] = None  # wander/sentinel/reminder 等事件类型标识
    eventParentId: Optional[str] = None  # non-null only for associated event rows
    notificationMetadata: Optional[Dict[str, Any]] = None  # UI-only notification routing facts
    images: Optional[List[Dict[str, Any]]] = None  # 图片附件 [{id, url, filename, size, type}]
    voiceAttachment: Optional[Dict[str, Any]] = None  # 语音附件 {"path":..., "text":..., "duration":...}
    longTextCard: Optional[Dict[str, str]] = None  # 长文小卡片 {id,title,body}


class Session(BaseModel):
    """前端会话模型"""
    id: str
    title: str
    messages: List[Message] = []
    createdAt: str  # ISO格式
    updatedAt: str  # ISO格式
    locked: bool = False


class SessionManager:
    """会话管理器：负责读写前端会话数据"""

    def __init__(self, data_dir: str = "sessions"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        # 存储所有会话的索引文件
        self.index_file = os.path.join(data_dir, "sessions_index.json")

        # 初始化索引
        self._ensure_index_file()

        # 每个会话的写锁，防止并发写入导致文件损坏
        self._session_locks: Dict[str, asyncio.Lock] = {}

        # 写缓冲：减少高频 update_session 调用的磁盘 I/O
        # session_id -> 待写入的完整会话数据
        self._write_buffers: Dict[str, Dict[str, Any]] = {}
        # session_id -> 延迟刷盘任务
        self._flush_tasks: Dict[str, asyncio.Task] = {}
        # 少数工具会话只需更新元数据，不应因此成为“最近私聊”。
        # 标记仅保留到对应缓冲成功刷盘；任何后续普通更新会取消它。
        self._preserve_updated_at_flush: set[str] = set()
        # debounce 延迟（秒）
        self._flush_delay: float = 1.5

    def _ensure_index_file(self) -> None:
        """确保索引文件存在"""
        if not os.path.exists(self.index_file):
            default_index = {
                "sessions": [],
                "current_session_id": "",
                "last_updated": now_iso()
            }
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(default_index, f, ensure_ascii=False, indent=2)

    def _get_session_file_path(self, session_id: str) -> str:
        """获取会话数据文件路径"""
        # 使用session_id的哈希值作为文件名，避免非法字符
        filename = hashlib.md5(session_id.encode()).hexdigest() + ".json"
        return os.path.join(self.data_dir, filename)

    def _get_metadata_file_path(self, session_id: str) -> str:
        return self._get_session_file_path(session_id) + ".meta"

    @staticmethod
    def _metadata_fields(session_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": session_id,
            "title": data.get("title") or "未命名会话",
            "createdAt": data.get("createdAt") or now_iso(),
            "updatedAt": data.get("updatedAt") or now_iso(),
            "locked": bool(data.get("locked", False)),
        }

    def _write_metadata_snapshot(self, session_id: str, data: Dict[str, Any]) -> None:
        """元数据是可重建缓存；用文件 stat 校验，避免崩溃后读到旧标题。"""
        session_path = self._get_session_file_path(session_id)
        stat = os.stat(session_path)
        snapshot = {
            **self._metadata_fields(session_id, data),
            "file_mtime_ns": stat.st_mtime_ns,
            "file_size": stat.st_size,
        }
        path = self._get_metadata_file_path(session_id)
        tmp_path = path + f".{uuid.uuid4().hex}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False)
        os.replace(tmp_path, path)

    async def get_session_metadata(self, session_id: str) -> Optional[Session]:
        """只读接口的轻量元数据路径；消息仍一律从 SQLite 读取。"""
        session_path = self._get_session_file_path(session_id)
        if not os.path.exists(session_path):
            return None
        buffered = self._write_buffers.get(session_id)
        if buffered is not None:
            return Session(**self._metadata_fields(session_id, buffered), messages=[])
        try:
            stat = os.stat(session_path)
            with open(self._get_metadata_file_path(session_id), "r", encoding="utf-8") as handle:
                snapshot = json.load(handle)
            if (snapshot.get("id") == session_id
                    and snapshot.get("file_mtime_ns") == stat.st_mtime_ns
                    and snapshot.get("file_size") == stat.st_size):
                return Session(**self._metadata_fields(session_id, snapshot), messages=[])
        except (OSError, ValueError, TypeError):
            pass
        data = await self._load_session_file(session_id)
        if not data:
            return None
        try:
            self._write_metadata_snapshot(session_id, data)
        except OSError as exc:
            logger.warning("会话元数据快照写入失败 %s: %s", session_id, exc)
        return Session(**self._metadata_fields(session_id, data), messages=[])

    async def get_all_session_metadata(self) -> List[Session]:
        index = await self._load_index()
        sessions = []
        for session_id in index.get("sessions", []):
            if not is_visible_session(session_id):
                continue
            session = await self.get_session_metadata(session_id)
            if session is not None:
                sessions.append(session)
        sessions.sort(key=lambda session: session.updatedAt, reverse=True)
        return sessions

    async def _load_index(self) -> Dict[str, Any]:
        """异步加载索引文件"""
        try:
            async with aiofiles.open(self.index_file, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content) if content else {"sessions": [], "current_session_id": ""}
        except Exception as e:
            print(f"加载索引文件失败: {e}")
            return {"sessions": [], "current_session_id": ""}

    async def get_primary_session_id(self) -> str:
        """索引中首个仍存在的私聊会话是固定主聊天；切换当前会话不会改变它。"""
        index_data = await self._load_index()
        for session_id in index_data.get("sessions", []):
            if is_chat_session(session_id) and os.path.exists(self._get_session_file_path(session_id)):
                return session_id
        return ""

    async def _save_index(self, index_data: Dict[str, Any]) -> None:
        """异步保存索引文件"""
        try:
            index_data["last_updated"] = now_iso()
            async with aiofiles.open(self.index_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(index_data, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"保存索引文件失败: {e}")
            raise

    async def _load_session_file(self, session_id: str) -> Dict[str, Any]:
        """异步加载会话文件"""
        filepath = self._get_session_file_path(session_id)
        if not os.path.exists(filepath):
            return {}

        try:
            async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
                content = await f.read()
                return await asyncio.to_thread(json.loads, content) if content else {}
        except Exception as e:
            print(f"加载会话文件失败 {session_id}: {e}")
            return {}

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """获取 per-session 异步锁，用于串行化读写操作"""
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def _save_session_file(self, session_id: str, session_data: Dict[str, Any],
                                 preserve_updated_at: bool = False) -> None:
        """异步保存会话文件（注意：调用方需自行加锁 _get_session_lock）

        使用原子写入：先写 .tmp 再 os.replace，防止写入中断导致文件截断为 0 字节。
        """
        try:
            filepath = self._get_session_file_path(session_id)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            if not preserve_updated_at:
                session_data["updatedAt"] = now_iso()
            tmp_path = filepath + ".tmp"

            payload = await asyncio.to_thread(json.dumps, session_data, ensure_ascii=False, indent=2)
            async with aiofiles.open(tmp_path, 'w', encoding='utf-8') as f:
                await f.write(payload)
            os.replace(tmp_path, filepath)
            try:
                self._write_metadata_snapshot(session_id, session_data)
            except OSError as exc:
                logger.warning("会话元数据快照写入失败 %s: %s", session_id, exc)
        except Exception as e:
            logger.error(f"保存会话文件失败 {session_id}: {e}")
            raise

    def _normalize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """补齐历史脏数据中缺失的消息字段。递归序列化嵌套 Pydantic 对象。"""
        normalized_messages = []
        base_ts = int(datetime.now().timestamp() * 1000)

        for index, msg in enumerate(messages or []):
            # 递归转 dict：Pydantic v2 model_dump > v1 dict > 原生 dict
            if hasattr(msg, 'model_dump'):
                normalized = msg.model_dump()
            elif hasattr(msg, 'dict') and callable(msg.dict):
                normalized = msg.dict()
            else:
                normalized = dict(msg)
            # 确保 toolCalls 内的 ToolCallRecord 也被序列化
            if 'toolCalls' in normalized and isinstance(normalized['toolCalls'], list):
                _serialized_tcs = []
                for tc in normalized['toolCalls']:
                    if hasattr(tc, 'model_dump'):
                        _serialized_tcs.append(tc.model_dump())
                    elif hasattr(tc, 'dict') and callable(tc.dict):
                        _serialized_tcs.append(tc.dict())
                    elif isinstance(tc, dict):
                        _serialized_tcs.append(tc)
                    elif hasattr(tc, '__dict__'):
                        _serialized_tcs.append(dict(tc))
                    else:
                        _serialized_tcs.append(tc)
                # 清洗残记录：toolCalls 元素缺失 tool/name 标识（如生图双写不一致遗留的空 dict {}）
                # 会让 Session Pydantic 校验失败 → 整条会话列表 500。SQLite 权威源保留完整数据，
                # 这里剥掉残壳即可恢复，前端消息从 SQLite 直读不受影响（2026-09-03）。
                normalized['toolCalls'] = [
                    tc for tc in _serialized_tcs
                    if isinstance(tc, dict) and (tc.get('tool') or tc.get('name'))
                ]
            normalized.setdefault("id", str(base_ts + index))
            normalized.setdefault("timestamp", now_iso())
            normalized.setdefault("role", "user")
            normalized.setdefault("content", "")
            normalized_messages.append(normalized)

        return normalized_messages

    def _normalize_session_data(self, session_id: str, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """补齐会话结构中的必要字段，兼容旧版本写入的数据。"""
        normalized = dict(session_data or {})
        normalized["id"] = normalized.get("id", session_id)
        normalized["title"] = normalized.get("title", "未命名会话")
        normalized["createdAt"] = normalized.get("createdAt", now_iso())
        normalized["updatedAt"] = normalized.get("updatedAt", now_iso())
        normalized["messages"] = self._normalize_messages(normalized.get("messages", []))
        normalized["messages"].sort(key=_msg_sort_key)
        normalized["locked"] = normalized.get("locked", False)
        return normalized

    async def get_all_sessions(self) -> List[Session]:
        """获取所有会话"""
        index_data = await self._load_index()
        sessions = []

        for session_id in index_data.get("sessions", []):
            if not is_visible_session(session_id):
                continue
            # 优先从写缓冲读取（包含尚未刷盘的更新）
            if session_id in self._write_buffers:
                session_data = self._write_buffers[session_id]
            else:
                session_data = await self._load_session_file(session_id)
            if session_data:
                session_data = self._normalize_session_data(session_id, session_data)
                sessions.append(Session(**session_data))
            else:
                logger.warning(f"会话文件为空或损坏，已跳过: {session_id}")

        # 按更新时间倒序排列
        sessions.sort(key=lambda s: s.updatedAt, reverse=True)
        return sessions

    async def get_session(self, session_id: str) -> Optional[Session]:
        """获取特定会话（优先读取写缓冲，保证数据一致性）"""
        # 优先从写缓冲读取（包含尚未刷盘的更新）
        if session_id in self._write_buffers:
            session_data = self._write_buffers[session_id]
            session_data = self._normalize_session_data(session_id, dict(session_data))
            return await self._with_source_receipts(session_id, session_data)

        session_data = await self._load_session_file(session_id)
        if not session_data:
            return None

        session_data = self._normalize_session_data(session_id, session_data)

        return await self._with_source_receipts(session_id, session_data)

    async def _with_source_receipts(self, session_id: str, data: dict) -> Session:
        from api_sources.message_sources import for_session
        receipts = await asyncio.to_thread(for_session, session_id)
        session = Session(**data)
        for message in session.messages:
            message.modelSource = receipts.get(message.id)
        return session

    async def rebuild_from_sqlite(self, session_id: str) -> Optional[Session]:
        """从 SQLite 全量重建 session JSON 缓存。"""
        try:
            from event_chronicle import get_global_chronicle
            chronicle = get_global_chronicle()
            rows = await asyncio.to_thread(
                chronicle.get_messages_by_session_id, session_id, 100000, desc=False
            )
        except Exception as e:
            logger.error(f"rebuild_from_sqlite 失败: {e}")
            return None

        if not rows:
            return None

        existing = await self._load_session_file(session_id)
        existing_messages = (existing or {}).get("messages", [])
        existing_by_id = {
            (item.get("id") if isinstance(item, dict) else getattr(item, "id", "")): item
            for item in existing_messages
        }
        messages = []
        from api_sources.message_sources import for_session
        source_receipts = for_session(session_id)
        for r in rows:
            message_id = r.get("message_id") or f"msg_{r.get('timestamp', '')}"
            msg = {
                "id": message_id,
                "role": r.get("role", "user"),
                "content": r.get("content", ""),
                "timestamp": r.get("timestamp", ""),
                "reasoning_content": r.get("reasoning_content"),
                "isWander": bool(r.get("is_wander")),
                "isSentinel": bool(r.get("is_sentinel")),
                "isReminder": bool(r.get("is_reminder")),
                "isMusicCochlea": bool(r.get("is_music_cochlea")),
                "eventType": r.get("event_type", ""),
                "eventParentId": r.get("event_parent_id"),
            }
            previous = existing_by_id.get(message_id)
            if source_receipts.get(message_id):
                msg["modelSource"] = source_receipts[message_id]
            previous_metadata = previous.get("notificationMetadata") if isinstance(previous, dict) else getattr(previous, "notificationMetadata", None)
            if previous_metadata:
                msg["notificationMetadata"] = previous_metadata
            # 图片/语音附件
            for db_key, js_key in [("images", "images"), ("voice_attachment", "voiceAttachment")]:
                if r.get(db_key):
                    try:
                        import json as _j
                        msg[js_key] = _j.loads(r[db_key]) if isinstance(r[db_key], str) else r[db_key]
                    except Exception:
                        pass
            if r.get("long_text_card"):
                try:
                    import json as _j
                    msg["longTextCard"] = _j.loads(r["long_text_card"]) if isinstance(r["long_text_card"], str) else r["long_text_card"]
                except Exception:
                    pass
            # 思考链
            tcj = r.get("thinking_chain_json")
            if tcj:
                try:
                    import json as _j
                    msg["thinkingChain"] = _j.loads(tcj)
                except Exception:
                    pass
            if "thinkingChain" not in msg and r.get("reasoning_content"):
                msg["thinkingChain"] = [{"content": r["reasoning_content"], "timestamp": r.get("timestamp", "")}]
            # 工具调用（剥离 voice_audio_base64 大字段）
            tc = r.get("tool_calls")
            if tc:
                try:
                    import json as _j
                    parsed = _j.loads(tc) if isinstance(tc, str) else tc
                    msg["toolCalls"] = strip_voice_base64(parsed)
                except Exception:
                    pass
            messages.append(msg)

        merged = {
            **(existing or {}),
            "id": session_id,
            "messages": messages,
            "updatedAt": datetime.now().isoformat(),
        }
        self._write_buffers[session_id] = merged
        await self.flush_session(session_id)
        logger.info(f"rebuild_from_sqlite: {session_id[:20]} -> {len(messages)} 条")
        return Session(**merged)

    async def create_session(self, session_data: Dict[str, Any]) -> Session:
        """创建新会话"""
        session_id = session_data.get("id")
        if not session_id:
            # 生成新的会话ID
            session_id = f"session_{int(datetime.now().timestamp() * 1000)}_{hashlib.md5(str(datetime.now()).encode()).hexdigest()[:8]}"
            session_data["id"] = session_id

        # 确保时间戳
        if "createdAt" not in session_data:
            session_data["createdAt"] = now_iso()
        if "updatedAt" not in session_data:
            session_data["updatedAt"] = now_iso()

        # 确保标题
        if "title" not in session_data or not session_data["title"]:
            session_data["title"] = "新会话"

        # 确保消息列表
        if "messages" not in session_data:
            session_data["messages"] = []

        # 保存会话文件
        await self._save_session_file(session_id, session_data)

        # 更新索引
        index_data = await self._load_index()
        sessions_list = index_data.get("sessions", [])
        if session_id not in sessions_list:
            sessions_list.append(session_id)
            index_data["sessions"] = sessions_list

        # 如果没有当前会话ID，设置为新会话
        if not index_data.get("current_session_id") and sessions_list:
            index_data["current_session_id"] = session_id

        await self._save_index(index_data)

        return Session(**session_data)

    async def _do_flush(self, session_id: str) -> None:
        """原子地读取缓冲区并写入磁盘。在锁内完成 读→合并→写→清除，防止与 update_session 的数据竞争。"""
        async with await self._get_session_lock(session_id):
            data = self._write_buffers.get(session_id)
            if not data:
                return
            existing_data = await self._load_session_file(session_id)
            merged_data = {**(existing_data or {}), **data}
            merged_data["id"] = session_id
            preserve_updated_at = session_id in self._preserve_updated_at_flush
            if not preserve_updated_at:
                merged_data["updatedAt"] = now_iso()
            merged_data = await asyncio.to_thread(self._normalize_session_data, session_id, merged_data)
            await self._save_session_file(
                session_id,
                merged_data,
                preserve_updated_at=preserve_updated_at,
            )
            self._write_buffers.pop(session_id, None)
            self._preserve_updated_at_flush.discard(session_id)

    async def _flush_session_buffer(self, session_id: str):
        """延迟刷盘：等待 debounce 间隔后将缓冲数据写入磁盘。失败时数据保留在缓冲区。"""
        await asyncio.sleep(self._flush_delay)
        if session_id in self._write_buffers:
            try:
                await self._do_flush(session_id)
            except Exception:
                logger.error(f"刷盘失败 {session_id}，数据保留在缓冲区等待重试")
                return  # 保留缓冲区数据
        if session_id in self._flush_tasks:
            del self._flush_tasks[session_id]

    async def flush_session(self, session_id: str):
        """立即刷盘指定会话（锁切换等关键操作后调用，防止 debounce 窗口内丢数据）"""
        if session_id in self._flush_tasks:
            self._flush_tasks[session_id].cancel()
            del self._flush_tasks[session_id]
        if session_id in self._write_buffers:
            await self._do_flush(session_id)

    async def flush_all_buffers(self):
        """立即刷盘所有待写入数据（shutdown 时调用，确保零数据丢失）"""
        for session_id in list(self._write_buffers.keys()):
            if session_id in self._flush_tasks:
                self._flush_tasks[session_id].cancel()
                del self._flush_tasks[session_id]
            await self._do_flush(session_id)

    async def append_message(self, session_id: str, message: Dict[str, Any]) -> bool:
        """Atomically append one validated message without rebuilding Session.

        Main chat only needs an idempotent append before the provider call.
        Routing that operation through ``get_session`` + ``update_session``
        constructs Pydantic models for the entire history twice, which becomes
        costly for long-lived sessions.  Full normalization still happens in
        the debounced flush, outside the response-critical path.
        """
        if not session_id or not isinstance(message, dict):
            return False

        validated = Message(**message)
        if hasattr(validated, "model_dump"):
            message_data = validated.model_dump(exclude_none=True)
        else:
            message_data = validated.dict(exclude_none=True)
        message_id = str(message_data.get("id") or "")
        if not message_id:
            return False

        async with await self._get_session_lock(session_id):
            if session_id in self._write_buffers:
                existing_data = self._write_buffers[session_id]
            else:
                existing_data = await self._load_session_file(session_id)
            if not existing_data:
                return False

            messages = list(existing_data.get("messages", []) or [])
            for existing in reversed(messages):
                existing_id = (
                    existing.get("id", "")
                    if isinstance(existing, dict)
                    else getattr(existing, "id", "")
                )
                if str(existing_id) == message_id:
                    return False

            messages.append(message_data)
            buffered = {
                **existing_data,
                "id": session_id,
                "messages": messages,
                "updatedAt": now_iso(),
            }
            self._write_buffers[session_id] = buffered
            self._preserve_updated_at_flush.discard(session_id)

        previous_flush = self._flush_tasks.pop(session_id, None)
        if previous_flush:
            previous_flush.cancel()
        self._flush_tasks[session_id] = asyncio.create_task(
            self._flush_session_buffer(session_id)
        )
        return True

    async def update_session(self, session_id: str, session_data: Dict[str, Any], merge: bool = True,
                             preserve_updated_at: bool = False) -> Optional[Session]:
        """更新会话（写缓冲 + debounce 合并写入，减少高频 I/O）
        merge=False 时跳过防截断合并，直接替换 messages（用于服务端删/撤/改操作）。"""
        # 取消该 session 之前的延迟刷盘任务
        if session_id in self._flush_tasks:
            self._flush_tasks[session_id].cancel()
            del self._flush_tasks[session_id]

        # per-session 锁保护读-改-写原子性，防止并发覆盖导致数据丢失
        async with await self._get_session_lock(session_id):
            # 合并到写缓冲：优先与已有缓冲合并，否则从磁盘加载
            if session_id in self._write_buffers:
                existing_data = self._write_buffers[session_id]
            else:
                existing_data = await self._load_session_file(session_id)
            if not existing_data:
                return None

            # 保护今天的新消息不被跨端 PUT 覆盖（移动端发消息后 PC 未刷新就 PUT）
            existing_msgs = existing_data.get("messages", [])
            incoming_msgs = session_data.get("messages")
            if merge and incoming_msgs is not None and existing_msgs and len(incoming_msgs) > 0:
                incoming_ids = set()
                for m in incoming_msgs:
                    mid = m.get("id") if isinstance(m, dict) else (getattr(m, 'id', None) if hasattr(m, 'id') else None)
                    if mid:
                        incoming_ids.add(mid)

                # 找出 existing 中有但 incoming 中没有的"今天"的消息
                today_missing = 0
                total_missing = 0
                today_str = datetime.now().strftime('%m/%d/%Y')
                today_iso = datetime.now().strftime('%Y-%m-%d')
                for m in existing_msgs:
                    mid = m.get("id") if isinstance(m, dict) else (getattr(m, 'id', None) if hasattr(m, 'id') else None)
                    if mid and mid not in incoming_ids:
                        total_missing += 1
                        ts = m.get("timestamp", "") if isinstance(m, dict) else (getattr(m, 'timestamp', '') if hasattr(m, 'timestamp') else '')
                        if ts.startswith(today_str) or ts.startswith(today_iso):
                            today_missing += 1

                # 任何缺失都触发 ID 合并保护（防止移动端截断覆盖完整数据）
                if total_missing > 0 or today_missing > 0:
                    if total_missing > 50:
                        logger.warning(
                            f"PUT 防截断：existing 中有 {total_missing} 条不在 incoming 中"
                            f"（其中今天: {today_missing}），按 ID 合并 (session={session_id[:20]}...)"
                        )
                    existing_by_id = {}
                    # 构建已有内容的快速去重索引 (role, content前100字符)
                    existing_content_keys = set()
                    now_ts = datetime.now()
                    for m in existing_msgs:
                        mid = m.get("id") if isinstance(m, dict) else (getattr(m, 'id', None) if hasattr(m, 'id') else None)
                        if mid:
                            existing_by_id[mid] = m
                            # 仅对60秒内的消息建立内容去重索引
                            ts = m.get("timestamp", "") if isinstance(m, dict) else (getattr(m, 'timestamp', '') if hasattr(m, 'timestamp') else '')
                            try:
                                if ts:
                                    # 尝试解析两种格式
                                    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                                        try:
                                            mt = datetime.strptime(ts[:19], fmt)
                                            if (now_ts - mt).total_seconds() < 60:
                                                role = m.get("role", "") if isinstance(m, dict) else (getattr(m, 'role', '') if hasattr(m, 'role') else '')
                                                content = m.get("content", "") if isinstance(m, dict) else (getattr(m, 'content', '') if hasattr(m, 'content') else '')
                                                existing_content_keys.add((role, content[:100]))
                                            break
                                        except ValueError:
                                            continue
                            except Exception:
                                pass
                    for m in incoming_msgs:
                        mid = m.get("id") if isinstance(m, dict) else (getattr(m, 'id', None) if hasattr(m, 'id') else None)
                        if mid and mid not in existing_by_id:
                            # 内容去重：同role+同content前100字符+60秒内 → 跳过
                            inc_role = m.get("role", "") if isinstance(m, dict) else (getattr(m, 'role', '') if hasattr(m, 'role') else '')
                            inc_content = m.get("content", "") if isinstance(m, dict) else (getattr(m, 'content', '') if hasattr(m, 'content') else '')
                            if (inc_role, inc_content[:100]) not in existing_content_keys:
                                existing_by_id[mid] = m
                                existing_content_keys.add((inc_role, inc_content[:100]))
                    session_data = {**session_data, "messages": list(existing_by_id.values())}

            merged_data = {**existing_data, **session_data}

            merged_data["id"] = session_id
            merged_data["updatedAt"] = (
                existing_data.get("updatedAt", now_iso())
                if preserve_updated_at else now_iso()
            )

            if "title" not in merged_data:
                merged_data["title"] = merged_data.get("title", "未命名会话")
            if "createdAt" not in merged_data:
                merged_data["createdAt"] = merged_data.get("createdAt", now_iso())
            if "messages" not in merged_data:
                merged_data["messages"] = merged_data.get("messages", [])

            merged_data = self._normalize_session_data(session_id, merged_data)
            self._write_buffers[session_id] = merged_data
            if preserve_updated_at:
                self._preserve_updated_at_flush.add(session_id)
            else:
                self._preserve_updated_at_flush.discard(session_id)

        # 在锁外调度延迟刷盘，避免阻塞其他请求
        self._flush_tasks[session_id] = asyncio.create_task(
            self._flush_session_buffer(session_id)
        )

        return Session(**merged_data)

    async def replace_messages(self, session_id: str, removed_ids: list[str], replacement: dict) -> None:
        """Align only affected cache rows after an authoritative resend transaction."""
        message = Message(**replacement).model_dump(exclude_none=True)
        removed = set(removed_ids)
        async with await self._get_session_lock(session_id):
            data = self._write_buffers.get(session_id)
            if data is None:
                data = await self._load_session_file(session_id)
            if not data:
                raise ValueError('session missing')
            remaining = [row for row in data.get('messages', [])
                         if (row.get('id') if isinstance(row, dict) else row.id) not in removed]
            replaced = False
            for index, row in enumerate(remaining):
                if (row.get('id') if isinstance(row, dict) else row.id) == message['id']:
                    remaining[index] = message
                    replaced = True
                    break
            if not replaced:
                remaining.append(message)
            self._write_buffers[session_id] = {**data, 'messages': remaining, 'updatedAt': now_iso()}
            self._preserve_updated_at_flush.discard(session_id)
        previous = self._flush_tasks.pop(session_id, None)
        if previous:
            previous.cancel()
        self._flush_tasks[session_id] = asyncio.create_task(self._flush_session_buffer(session_id))

    async def update_message_tool_calls(self, session_id: str, message_id: str, tool_calls: list) -> bool:
        """锁内定点更新单条消息的 toolCalls（语音注入 voice_path 后同步 session JSON 用）。

        不能用 update_session(merge=False)——它会整表替换 messages，TTS 后台生成期间若有并发新增
        消息会被覆盖丢失；merge=True 的防截断合并只追加缺失 ID、不更新已存在消息的 toolCalls。
        """
        async with await self._get_session_lock(session_id):
            if session_id in self._write_buffers:
                data = self._write_buffers[session_id]
            else:
                data = await self._load_session_file(session_id)
            if not data:
                return False
            msgs = data.get("messages", [])
            hit = False
            for m in msgs:
                mid = m.get("id") if isinstance(m, dict) else getattr(m, "id", "")
                if mid == message_id:
                    if isinstance(m, dict):
                        m["toolCalls"] = tool_calls
                    else:
                        m.toolCalls = tool_calls
                    hit = True
                    break
            if not hit:
                return False
            data["updatedAt"] = now_iso()
            self._write_buffers[session_id] = data
        self._flush_tasks[session_id] = asyncio.create_task(
            self._flush_session_buffer(session_id)
        )
        return True

    async def update_attachment_tool_call(self, session_id: str, message_id: str,
                                          tool_name: str, identity_key: str,
                                          identity_value: str, replacement: dict) -> bool:
        """Merge one committed media result into the display cache under its lock.

        Image and voice jobs may finish together.  Replacing an entire old
        toolCalls snapshot would put a sibling attachment back into pending.
        SQLite CAS remains the authority; this only converges the JSON mirror.
        """
        async with await self._get_session_lock(session_id):
            data = (self._write_buffers.get(session_id)
                    if session_id in self._write_buffers else await self._load_session_file(session_id))
            if not data:
                return False
            for message in data.get("messages", []):
                mid = message.get("id") if isinstance(message, dict) else getattr(message, "id", "")
                if mid != message_id:
                    continue
                raw_calls = message.get("toolCalls") if isinstance(message, dict) else getattr(message, "toolCalls", None)
                calls = [dict(call) if isinstance(call, dict) else call.model_dump() for call in raw_calls or []]
                for index, call in enumerate(calls):
                    extra = call.get("extra_data") if isinstance(call, dict) else None
                    if (call.get("tool") == tool_name and isinstance(extra, dict)
                            and str(extra.get(identity_key) or "") == identity_value):
                        calls[index] = replacement
                        if isinstance(message, dict):
                            message["toolCalls"] = calls
                        else:
                            message.toolCalls = calls
                        data["updatedAt"] = now_iso()
                        self._write_buffers[session_id] = data
                        self._flush_tasks[session_id] = asyncio.create_task(
                            self._flush_session_buffer(session_id))
                        return True
                return False
        return False

    async def delete_session(self, session_id: str) -> bool:
        """删除会话（带 per-session 锁，防止并发读写冲突；上锁的会话拒绝删除）"""
        async with await self._get_session_lock(session_id):
            if session_id == await self.get_primary_session_id():
                logger.warning("尝试删除固定主会话，已拒绝")
                return False
            # 检查会话是否存在
            session_file = self._get_session_file_path(session_id)
            if not os.path.exists(session_file):
                return False

            # 检查是否上锁
            session_data = await self._load_session_file(session_id)
            if session_data.get("locked", False):
                logger.warning(f"尝试删除已上锁的会话 {session_id}，已拒绝")
                return False

            try:
                # 删除会话文件
                os.remove(session_file)

                # 更新索引
                index_data = await self._load_index()
                sessions_list = index_data.get("sessions", [])
                if session_id in sessions_list:
                    sessions_list.remove(session_id)
                    index_data["sessions"] = sessions_list

                # 如果删除的是当前会话，重置当前会话ID
                if index_data.get("current_session_id") == session_id:
                    index_data["current_session_id"] = sessions_list[0] if sessions_list else ""

                await self._save_index(index_data)
                return True
            except Exception as e:
                print(f"删除会话失败 {session_id}: {e}")
                return False

    async def get_current_session_id(self) -> str:
        """获取当前会话ID，自动校验是否存在（不存在则回退到第一个可用会话）"""
        index_data = await self._load_index()
        current_id = index_data.get("current_session_id", "")
        sessions_list = index_data.get("sessions", [])

        # 校验 current_session_id 是否仍然有效
        if current_id:
            if current_id not in sessions_list or not is_visible_session(current_id):
                current_id = ""
            else:
                session_file = self._get_session_file_path(current_id)
                if not os.path.exists(session_file):
                    current_id = ""

        # 自动回退：若 current 无效但存在其他会话，使用第一个
        if not current_id and sessions_list:
            for sid in sessions_list:
                if is_visible_session(sid) and os.path.exists(self._get_session_file_path(sid)):
                    current_id = sid
                    index_data["current_session_id"] = current_id
                    await self._save_index(index_data)
                    break

        return current_id

    async def set_current_session_id(self, session_id: str) -> bool:
        """设置当前会话ID"""
        # 退役门户仍保留原文件，但不能重新成为当前会话。
        if not is_visible_session(session_id):
            return False
        # 检查会话是否存在
        session_file = self._get_session_file_path(session_id)
        if not os.path.exists(session_file):
            return False

        index_data = await self._load_index()
        index_data["current_session_id"] = session_id
        await self._save_index(index_data)
        return True

    async def clear_all_sessions(self) -> bool:
        """清除可删除会话；固定主会话和上锁会话保留。"""
        try:
            index_data = await self._load_index()
            sessions_list = index_data.get("sessions", [])
            primary_session_id = await self.get_primary_session_id()

            # 删除所有未上锁的会话文件
            remaining = []
            for session_id in sessions_list:
                session_file = self._get_session_file_path(session_id)
                if os.path.exists(session_file):
                    if session_id == primary_session_id:
                        remaining.append(session_id)
                        continue
                    session_data = await self._load_session_file(session_id)
                    if session_data.get("locked", False):
                        remaining.append(session_id)
                        continue
                    os.remove(session_file)
                # 文件不存在也从列表中移除
            # else 保留 locked 的 session

            # 重置索引（保留上锁的会话）
            index_data["sessions"] = remaining
            if index_data.get("current_session_id") not in remaining:
                index_data["current_session_id"] = remaining[0] if remaining else ""
            index_data["last_updated"] = now_iso()

            await self._save_index(index_data)
            return True
        except Exception as e:
            print(f"清除所有会话失败: {e}")
            return False


# 全局会话管理器实例
_session_manager_instance = None


def get_global_session_manager() -> SessionManager:
    """获取全局会话管理器实例"""
    global _session_manager_instance
    if _session_manager_instance is None:
        _session_manager_instance = SessionManager()
    return _session_manager_instance


async def initialize_session_manager() -> None:
    """初始化会话管理器（确保目录和索引存在）"""
    manager = get_global_session_manager()
    # 确保索引文件存在
    manager._ensure_index_file()
    print("会话管理器初始化完成")
