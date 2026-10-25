"""
按日期分区的对话日志管理器
将对话日志按日期存储，支持当天（short_term）和最近7天（mid_term）的分离
"""
import os
import json
import hashlib
import logging
from datetime import datetime, timedelta
from time_utils import now_iso, parse_ts
from typing import List, Dict, Any, Optional, Tuple
import aiofiles
import asyncio

logger = logging.getLogger(__name__)


def resolve_active_date(session_id: str) -> str:
    """解析当前应归属的活跃日（YYYY-MM-DD）。

    有活跃话题 → 话题开始日的日期（now_iso 生成的 +08:00/naive 北京时间戳，前 10 位即北京日期）；
    无活跃话题 → 今天日历日。
    AI 的主动推送（漫想/哨兵/提醒）写消息时必须显式传 active_date（trap 170 落地），
    不能依赖 topic_state.start_time 推导——start_time 若被陈旧话题污染会把 AI 消息写进错误活跃日。
    """
    try:
        from event_chronicle import get_global_chronicle
        ts = get_global_chronicle().get_topic_state(session_id)
        if ts and ts.get("is_active") and ts.get("start_time"):
            return str(ts["start_time"])[:10]
    except Exception:
        pass
    return datetime.now().strftime("%Y-%m-%d")


class ConversationManagerV2:
    """按日期分区的对话日志管理器"""

    def __init__(self, data_dir: str = "conversations"):
        self.data_dir = data_dir
        self.logs_dir = os.path.join(data_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

        # 创建today和recent目录
        self.today_dir = os.path.join(self.logs_dir, "today")
        self.recent_dir = os.path.join(self.logs_dir, "recent")
        os.makedirs(self.today_dir, exist_ok=True)
        os.makedirs(self.recent_dir, exist_ok=True)

        # 更新今天的符号链接
        self._update_today_link()

        # 按 session 的写入锁，防止 read-modify-write 竞态导致消息丢失
        self._write_locks: Dict[str, asyncio.Lock] = {}

    def _get_write_lock(self, session_id: str) -> asyncio.Lock:
        """获取或创建指定 session 的写入锁"""
        if session_id not in self._write_locks:
            self._write_locks[session_id] = asyncio.Lock()
        return self._write_locks[session_id]

    def _update_today_link(self) -> None:
        """更新today目录指向当天日期目录"""
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_date_dir = os.path.join(self.logs_dir, today_str)

        # 如果当天日期目录不存在，创建它
        if not os.path.exists(today_date_dir):
            os.makedirs(today_date_dir, exist_ok=True)

        # 在Unix系统上创建符号链接，Windows上复制或直接使用日期目录
        # 简化：直接使用日期目录，today作为别名目录
        # 我们将today_dir视为今天的日期目录
        # 实际实现中，我们可以将today_dir重定向到日期目录
        # 这里我们直接使用日期目录，但为了兼容性保留today_dir

        # 对于Windows，我们创建today目录并复制内容？或者直接使用日期目录
        # 简化：将today_dir作为今天的日期目录（每次调用时更新）
        # 实际上，我们可以让today_dir成为日期目录的符号链接
        # 但Windows符号链接需要权限，我们选择直接使用日期目录

        # 我们将today_dir设置为日期目录路径
        self.today_dir = today_date_dir

    def _get_session_hash(self, session_id: str) -> str:
        """获取会话ID的哈希值"""
        return hashlib.md5(session_id.encode()).hexdigest()

    def _get_today_date_str(self) -> str:
        """获取今天日期字符串"""
        return datetime.now().strftime("%Y-%m-%d")

    def _get_log_file_path(self, session_id: str, date_str: Optional[str] = None) -> str:
        """获取日志文件路径

        Args:
            session_id: 会话ID
            date_str: 日期字符串（YYYY-MM-DD），如果为None则使用今天
        """
        if date_str is None:
            date_str = self._get_today_date_str()

        # 日期目录
        date_dir = os.path.join(self.logs_dir, date_str)
        os.makedirs(date_dir, exist_ok=True)

        # 文件名
        filename = self._get_session_hash(session_id) + ".json"
        return os.path.join(date_dir, filename)

    def _is_recent_date(self, date_str: str) -> bool:
        """判断日期是否为最近7天（不包括今天）"""
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            delta = today - date
            return 1 <= delta.days <= 7
        except ValueError:
            return False

    def _get_bucket_for_date(self, date_str: str) -> str:
        """根据日期确定记忆桶类型

        Args:
            date_str: 日期字符串 (YYYY-MM-DD)

        Returns:
            'short_term' (当天), 'mid_term' (最近7天), 或 'long_term' (更早)
        """
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            delta = today - date

            if delta.days == 0:
                return "short_term"
            elif 1 <= delta.days <= 7:
                return "mid_term"
            else:
                return "long_term"
        except ValueError:
            # 日期格式错误，默认为long_term
            return "long_term"

    async def add_message(self, session_id: str, role: str, content: str, message_id: Optional[str] = None, reasoning_content: Optional[str] = None, is_wander: bool = False, is_sentinel: bool = False, is_reminder: bool = False, tool_summary: Optional[str] = None, tool_calls: Optional[str] = None, active_date: Optional[str] = None, calendar_date: Optional[str] = None, event_type: str = "", images: Optional[str] = None, voice_attachment: Optional[str] = None, thinking_chain_json: Optional[str] = None, long_text_card: Optional[str] = None, require_persistence: bool = False, timestamp_override: Optional[str] = None) -> Dict[str, Any]:
        """添加消息到 SQLite（纯 SQLite 写入，无 V2 JSON 双写）。

        Args:
            active_date: 活跃日日期（YYYY-MM-DD）。传入时使用该日期，否则从 topic_state 推导，推导失败回退日历日。
                        用于确保跨午夜的消息仍归属到正确的活跃日。
            calendar_date: 日历日期（YYYY-MM-DD）。传入时使用该日期，否则使用今天日期。
            require_persistence: 核验权威消息回执，重试耗尽或同 ID 内容不一致时抛错。
            timestamp_override: 调用方已冻结的事件时间；通知的 SQLite、缓存和 WS 共用。
        """
        date_str = active_date if active_date else self._get_today_date_str()
        # 未传入 active_date 时，尝试从 topic_state 表推导（跨午夜归属到话题开始日）
        if not active_date:
            try:
                from event_chronicle import get_global_chronicle
                chronicle = get_global_chronicle()
                ts = chronicle.get_topic_state(session_id)
                if ts and ts.get("is_active") and ts.get("start_time"):
                    from time_utils import parse_ts
                    from zoneinfo import ZoneInfo
                    dt = parse_ts(ts["start_time"])
                    date_str = dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
            except Exception:
                pass  # 推导失败，保持日历日
        cal_date = calendar_date if calendar_date else self._get_today_date_str()
        msg_id = message_id or f"msg_{datetime.now().timestamp()}"
        timestamp = timestamp_override or now_iso()

        # 写入 SQLite（带重试）
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from event_chronicle import get_global_chronicle
                chronicle = get_global_chronicle()
                chronicle.add_conversation_message(
                    active_date=date_str,
                    calendar_date=cal_date,
                    session_id=session_id,
                    timestamp=timestamp,
                    role=role,
                    content=content,
                    reasoning_content=reasoning_content,
                    message_id=msg_id,
                    is_wander=1 if is_wander else 0,
                    is_sentinel=1 if is_sentinel else 0,
                    is_reminder=1 if is_reminder else 0,
                    tool_summary=tool_summary,
                    tool_calls=tool_calls,
                    event_type=event_type,
                    images=images,
                    voice_attachment=voice_attachment,
                    thinking_chain_json=thinking_chain_json,
                    long_text_card=long_text_card,
                )
                if require_persistence:
                    saved = chronicle.get_message_by_msg_id(msg_id)
                    expected = {"session_id": session_id, "role": role,
                                "content": content, "event_type": event_type or ""}
                    if not saved or any((saved.get(key) or "") != value for key, value in expected.items()):
                        raise RuntimeError("message_commit_not_confirmed")
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"SQLite 写入失败(已重试{max_retries}次): {e}")
                    if require_persistence:
                        raise
                else:
                    await asyncio.sleep(0.05 * (2 ** attempt))

        return {
            "id": msg_id,
            "role": role,
            "content": content,
            "timestamp": timestamp
        }

    async def add_messages_batch(self, session_id: str, messages_spec: List[Dict[str, Any]], active_date: Optional[str] = None, calendar_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """批量添加消息到 SQLite（纯 SQLite 写入）。

        Args:
            session_id: 会话ID
            messages_spec: 消息规格列表
            active_date: 活跃日日期（YYYY-MM-DD）
            calendar_date: 日历日期（YYYY-MM-DD）
        """
        date_str = active_date if active_date else self._get_today_date_str()
        cal_date = calendar_date if calendar_date else self._get_today_date_str()

        results = []
        for spec in messages_spec:
            msg_id = spec.get("message_id") or f"msg_{datetime.now().timestamp()}"
            msg = {
                "id": msg_id,
                "role": spec["role"],
                "content": spec["content"],
                "timestamp": now_iso()
            }
            results.append(msg)

        # 批量写入 SQLite（带重试）。底层 EventChronicle 一次事务提交，
        # 然后在统一入口末尾维护 Scene，避免逐条写入时重复/半成品切分。
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from event_chronicle import get_global_chronicle
                chronicle = get_global_chronicle()
                chronicle.add_messages_batch([
                    {
                        "active_date": date_str,
                        "calendar_date": cal_date,
                        "session_id": session_id,
                        "timestamp": results[i]["timestamp"],
                        "role": spec["role"],
                        "content": spec["content"],
                        "reasoning_content": spec.get("reasoning_content"),
                        "message_id": results[i]["id"],
                        "is_wander": 1 if spec.get("is_wander") else 0,
                        "is_sentinel": 1 if spec.get("is_sentinel") else 0,
                        "is_reminder": 1 if spec.get("is_reminder") else 0,
                        "tool_summary": spec.get("tool_summary"),
                        "tool_calls": spec.get("tool_calls"),
                        "event_type": spec.get("event_type", ""),
                        "images": spec.get("images"),
                        "voice_attachment": spec.get("voice_attachment"),
                        "thinking_chain_json": spec.get("thinking_chain_json"),
                        "long_text_card": spec.get("long_text_card"),
                    }
                    for i, spec in enumerate(messages_spec)
                ])
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"SQLite 批量写入失败(已重试{max_retries}次): {e}")
                else:
                    await asyncio.sleep(0.05 * (2 ** attempt))

        return results

    async def edit_message(self, session_id: str, message_id: str, new_content: str,
                           old_content: str = "", old_role: str = "") -> bool:
        """编辑消息（SQLite 优先，V2 JSON 扫描兜底）。"""
        # 主路径：SQLite 直接更新
        try:
            from event_chronicle import get_global_chronicle
            chronicle = get_global_chronicle()
            before = chronicle.get_message_by_msg_id(message_id)
            if chronicle.update_message_content(message_id, new_content):
                try:
                    if before and before.get("active_date"):
                        from scene_manager.scene import rebuild_day

                        rebuild_day(before["active_date"], chronicle=chronicle)
                except Exception as scene_exc:
                    logger.warning(f"edit_message Scene 重算失败（消息已更新）: {scene_exc}")
                return True
        except Exception as e:
            logger.error(f"edit_message SQLite 更新失败: {e}")

        # 回退：扫描 V2 JSON（内容前缀匹配）
        if old_content and old_role:
            prefix = old_content[:100] if len(old_content) >= 20 else old_content
            from datetime import timedelta
            today = datetime.now()
            for days_ago in range(30):
                date_str = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                log_file = self._get_log_file_path(session_id, date_str)
                if not os.path.exists(log_file):
                    continue
                lock = self._get_write_lock(session_id)
                async with lock:
                    log_data = await self._load_log_file(log_file)
                    for msg in log_data.get("messages", []):
                        if msg.get("role") == old_role and msg.get("content", "").startswith(prefix):
                            msg["content"] = new_content
                            await self._save_log_file(log_file, log_data)
                            try:
                                matched_id = msg.get("id")
                                if matched_id:
                                    chronicle = get_global_chronicle()
                                    chronicle.update_message_content(matched_id, new_content)
                                    try:
                                        from scene_manager.scene import rebuild_day

                                        rebuilt = chronicle.get_message_by_msg_id(matched_id)
                                        if rebuilt and rebuilt.get("active_date"):
                                            rebuild_day(rebuilt["active_date"], chronicle=chronicle)
                                    except Exception as scene_exc:
                                        logger.warning(f"edit_message 回退 Scene 重算失败: {scene_exc}")
                            except Exception as e:
                                logger.error(f"edit_message SQLite sync(content): {e}")
                            return True

        return False

    async def upsert_message(self, session_id: str, role: str, content: str,
                             message_id: str, reasoning_content: str = None,
                             is_wander: bool = False, is_sentinel: bool = False,
                             is_reminder: bool = False, tool_summary: str = None,
                             tool_calls: str = None, active_date: str = None,
                             calendar_date: str = None,
                             event_type: str = "",
                             images: str = None, voice_attachment: str = None,
                             thinking_chain_json: str = None,
                             long_text_card: str = None,
                             timestamp_override: str = None,
                             ) -> Dict[str, Any]:
        """幂等写入 SQLite：message_id 存在则更新，不存在则插入。"""
        date_str = active_date if active_date else self._get_today_date_str()
        cal_date = calendar_date if calendar_date else self._get_today_date_str()
        timestamp = timestamp_override or now_iso()

        try:
            from event_chronicle import get_global_chronicle
            chronicle = get_global_chronicle()
            chronicle.upsert_conversation_message(
                active_date=date_str, calendar_date=cal_date,
                session_id=session_id, timestamp=timestamp,
                role=role, content=content, reasoning_content=reasoning_content,
                message_id=message_id, is_wander=1 if is_wander else 0,
                is_sentinel=1 if is_sentinel else 0, is_reminder=1 if is_reminder else 0,
                tool_summary=tool_summary, tool_calls=tool_calls,
                event_type=event_type,
                images=images, voice_attachment=voice_attachment,
                thinking_chain_json=thinking_chain_json,
                long_text_card=long_text_card,
            )
        except Exception as e:
            logger.error(f"upsert_message SQLite 失败: {e}")

        return {"id": message_id, "role": role, "content": content, "timestamp": timestamp}

    async def get_messages(self, session_id: str, date_range: Optional[Tuple[str, str]] = None) -> List[Dict[str, Any]]:
        """获取会话消息（从 SQLite）。"""
        import asyncio
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        if date_range:
            start_date, end_date = date_range
            return await asyncio.to_thread(chronicle.get_messages_by_date_range, start_date, end_date)
        return await asyncio.to_thread(chronicle.get_messages_by_session_id, session_id, 10000)

    async def get_unsummarized_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """获取未总结的消息（从 SQLite）。"""
        return await self.get_messages(session_id)

    async def archive_old_logs(self) -> None:
        """归档旧日志：超过7天的移动到archive/，超过90天的archive/目录彻底删除"""
        import shutil
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        archive_dir = os.path.join(self.logs_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)

        # 第一阶段：超过7天的日志 → 移动到 archive/
        for date_str in os.listdir(self.logs_dir):
            date_dir = os.path.join(self.logs_dir, date_str)

            if not os.path.isdir(date_dir) or len(date_str) != 10:
                continue

            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                delta_days = (today - date).days

                if delta_days > 7:
                    dest = os.path.join(archive_dir, date_str)
                    if os.path.exists(dest):
                        shutil.rmtree(dest)
                    shutil.move(date_dir, dest)
                    print(f"已归档过期日志: {date_str} → archive/")
            except ValueError:
                continue

        # 第二阶段：超过90天的归档 → 彻底删除
        for date_str in os.listdir(archive_dir):
            archive_path = os.path.join(archive_dir, date_str)
            if not os.path.isdir(archive_path) or len(date_str) != 10:
                continue

            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                if (today - date).days > 90:
                    shutil.rmtree(archive_path)
                    print(f"已清理过期归档: archive/{date_str}")
            except ValueError:
                continue

    async def _load_log_file(self, filepath: str) -> Dict[str, Any]:
        """异步加载日志文件"""
        if not os.path.exists(filepath):
            return {}

        try:
            async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content) if content else {}
        except Exception as e:
            print(f"加载日志文件失败 {filepath}: {e}")
            return {}

    async def _save_log_file(self, filepath: str, data: Dict[str, Any]) -> None:
        """异步保存日志文件"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"保存日志文件失败 {filepath}: {e}")

    def get_log_file_for_summary(self, session_id: str, date_str: Optional[str] = None) -> Tuple[str, str, str]:
        """获取用于摘要的日志文件路径、日期和记忆桶类型

        Args:
            session_id: 会话ID
            date_str: 日期字符串 (YYYY-MM-DD)，如果为None则使用今天

        Returns:
            (log_file_path, date_str, bucket)
        """
        if date_str is None:
            date_str = self._get_today_date_str()

        log_file = self._get_log_file_path(session_id, date_str)
        bucket = self._get_bucket_for_date(date_str)
        return log_file, date_str, bucket

    async def clear_conversation_log(self, session_id: str, date_str: Optional[str] = None) -> bool:
        """清除指定会话的日志文件（话题结束后调用）

        Args:
            session_id: 会话ID
            date_str: 日期字符串 (YYYY-MM-DD)，如果为None则使用今天

        Returns:
            bool: 是否成功清除
        """
        try:
            if date_str is None:
                date_str = self._get_today_date_str()
            log_file = self._get_log_file_path(session_id, date_str)
            if os.path.exists(log_file):
                os.remove(log_file)
                logger.info(f"已清除会话日志文件: {log_file}")
                return True
            else:
                logger.warning(f"日志文件不存在，无需清除: {log_file}")
                return False
        except Exception as e:
            logger.error(f"清除会话日志失败: {e}")
            return False

    async def delete_messages(self, session_id: str, message_ids: List[str]) -> Dict[str, Any]:
        """删除指定消息（纯 SQLite）。"""
        deleted_count = 0
        try:
            from event_chronicle import get_global_chronicle
            chronicle = get_global_chronicle()
            affected_dates = {
                message.get("active_date")
                for message_id in (message_ids or [])
                for message in [chronicle.get_message_by_msg_id(message_id)]
                if message and message.get("active_date")
            }
            deleted_count = chronicle.delete_conversation_messages(list(message_ids))
            if deleted_count:
                try:
                    from scene_manager.scene import rebuild_day

                    for active_date in sorted(affected_dates):
                        rebuild_day(active_date, chronicle=chronicle)
                except Exception as scene_exc:
                    logger.warning(f"delete_messages Scene 重算失败（消息已删除）: {scene_exc}")
        except Exception as e:
            logger.error(f"delete_messages SQLite 失败: {e}")
        return {"deleted_count": deleted_count, "failed_ids": [], "affected_files": []}


# 全局实例
conversation_manager_v2 = ConversationManagerV2()
