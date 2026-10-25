"""用户状态数据源 — 事件驱动，从 shared_state 读取（非轮询 wander_manager）"""

import logging

from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)


class UserStatusSource(DataSource):
    """用户声明状态 — 由 main.py:set_user_status_combined 事件驱动更新。"""

    name = "user_status"

    async def read(self) -> DataPoint:
        try:
            from shared_state import get_current_user_status, get_current_status_meta
            from wander_manager.user_status import get_current_status_since
            status = get_current_user_status()
            if status:
                status_since = get_current_status_since()
                if not status_since:
                    from shared_state import get_current_user_status_ts
                    status_since = get_current_user_status_ts()
                data = {"user_status": status, "status_since": status_since}
                # 附带 StatusMeta（供 behavior_mode 等消费者使用）
                meta_dict = get_current_status_meta()
                if meta_dict:
                    data["status_meta"] = meta_dict
                return DataPoint(source=self.name, data=data, captured_at=status_since)
            # 首次未设置时，回退到 wander_manager（只回退一次）
            from wander_manager.user_status import get_user_status, get_status_meta
            s = get_user_status()
            val = s.value if hasattr(s, 'value') else str(s)
            # 同步到 shared_state 供后续读取
            from shared_state import set_current_user_status, set_current_status_meta
            set_current_user_status(val)
            try:
                meta = get_status_meta()
                set_current_status_meta(meta.to_dict())
            except Exception:
                pass
            status_since = get_current_status_since()
            if not status_since:
                from shared_state import get_current_user_status_ts
                status_since = get_current_user_status_ts()
            data = {"user_status": val, "status_since": status_since}
            meta_dict = get_current_status_meta()
            if meta_dict:
                data["status_meta"] = meta_dict
            return DataPoint(source=self.name, data=data, captured_at=status_since)
        except Exception as e:
            logger.debug(f"UserStatusSource 读取失败: {e}")
            return DataPoint(source=self.name)

    @property
    def connection_state(self) -> str:
        return "connected"
