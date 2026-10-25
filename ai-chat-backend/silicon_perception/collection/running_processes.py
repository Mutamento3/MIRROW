"""运行进程数据源 — 枚举 PC 所有进程，排除系统/黑名单，得出"电脑开着什么"

2026-08-23 新增（人类伙伴反馈：apex 等后台进程识别不到——前台窗口只看最上层，游戏挂后台就漏）。
psutil 全进程扫描，ProcessCatalog.running_apps() 分类为 游戏/编码/应用，排除系统进程与启动器黑名单。
afk / 锁屏照样能读（进程枚举 ≠ 截屏）。
"""
import asyncio
import logging
from datetime import datetime
from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)


def _summarize(apps) -> str:
    """把运行进程列表压成可读摘要（'Apex Legends, Discord, Code'）。"""
    try:
        if not apps:
            return ""
        names = [a.get("name") or a.get("exe") for a in apps if isinstance(a, dict)]
        return ", ".join(names[:6])
    except Exception:
        return ""


class RunningProcessesSource(DataSource):
    """PC 运行进程 — 5min 轮询（psutil 全进程，游戏/编码/应用分类）"""

    name = "running_processes"
    tick_interval = 5  # 每 5 个基础 tick（5min）

    def __init__(self):
        self._last_apps: list = []
        self._last_summary: str = ""
        self._consecutive_failures: int = 0
        self._last_success_time = None
        self._last_error = None

    async def read(self) -> DataPoint:
        try:
            loop = asyncio.get_event_loop()
            apps = await loop.run_in_executor(None, self._scan)
            self._last_apps = apps or []
            self._last_summary = _summarize(self._last_apps)
            if not apps:
                self._last_error = None
                self._consecutive_failures = 0
                self._last_success_time = datetime.now().isoformat()
            else:
                self._consecutive_failures = 0
                self._last_error = None
                self._last_success_time = datetime.now().isoformat()
            return DataPoint(
                source=self.name,
                data={
                    "available": True,
                    "apps": self._last_apps,
                    "summary": self._last_summary,
                },
                captured_at=self._last_success_time,
            )
        except Exception as e:
            self._consecutive_failures += 1
            self._last_error = f"进程扫描异常: {str(e)[:80]}"
            logger.debug(f"RunningProcessesSource 读取失败: {e}")
            return DataPoint(source=self.name, data={
                "available": False, "apps": self._last_apps, "summary": self._last_summary,
            }, captured_at=self._last_success_time)

    def _scan(self) -> list:
        try:
            from silicon_perception.state.process_catalog import get_catalog
            return get_catalog().running_apps(top_n=8)
        except Exception as e:
            logger.debug(f"running_apps 失败: {e}")
            return []

    @property
    def connection_state(self) -> str:
        if self._consecutive_failures >= 3:
            return "failed"
        if self._last_apps:
            return "connected"
        return "idle"

    @property
    def last_error(self):
        return self._last_error
