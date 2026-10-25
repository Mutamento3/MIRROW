"""生理期数据源"""

import logging
from datetime import datetime

from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)


class PeriodSource(DataSource):
    """生理期数据源 — 读取 calendar_manager"""

    name = "period"

    async def read(self) -> DataPoint:
        try:
            from calendar_manager.database import get_period_info_today
            info = get_period_info_today()
            return DataPoint(source=self.name, data={
                # calendar_manager's public schema is active/day_number.
                # Keep the sentinel-internal names stable for existing rules,
                # storage and snapshot consumers.
                "is_period": info.get("active", False) if info else False,
                "period_day": info.get("day_number") if info else None,
            }, captured_at=datetime.now().isoformat())
        except Exception as e:
            logger.debug(f"PeriodSource 读取失败: {e}")
            return DataPoint(source=self.name)

    @property
    def connection_state(self) -> str:
        return "connected"
