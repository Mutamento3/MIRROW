"""手机通知数据源 — 当前通知集合，变化事件刷新与轮询兜底。

2026-08-22 新增：扩展 MirrowNotificationListener 为通用通知读取后，
哨兵可感知人类伙伴手机上收到的通知（微信/短信等 App）。
数据源：mobile_notifications relay（读取通知监听服务的手机本地缓存）。

隐私/成本克制：DataSnapshot 只存「包名 × 数量」摘要（_summarize_notifications），
不把通知全文注入哨兵/漫想上下文；完整内容仅 check_phone 等按需工具读取。
"""
import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)

_notifications_source = None
_FRESH_WINDOW_MS = 30 * 60_000


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def normalize_notifications(result: dict, now_ms=None) -> list:
    """Preserve original posting time; a new relay read cannot rejuvenate old text.

    Old APKs return a recent-event cache, not an authoritative active snapshot.
    Only a current native snapshot can keep a long-lived ongoing notification.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    observed = _integer(result.get("observed_at_ms"))
    current = (result.get("snapshot_scope") == "active_notifications"
               and 0 < observed <= now_ms + 60_000 and now_ms - observed <= 120_000)
    items = result.get("data")
    if not isinstance(items, list):
        return []
    cleaned = []
    for n in items:
        if not isinstance(n, dict):
            continue
        pkg = str(n.get("package") or "").strip()
        posted = _integer(n.get("time"))
        active = current and n.get("active") is True
        if current and not active:
            continue
        ongoing = n.get("ongoing") in (True, 1, "1")
        if (not pkg or posted <= 0 or posted > now_ms + 60_000
                or (now_ms - posted > _FRESH_WINDOW_MS and not (active and ongoing))):
            continue
        cleaned.append({
            "package": pkg, "title": str(n.get("title") or "")[:80],
            "text": str(n.get("text") or "")[:120], "time": str(posted),
            "category": str(n.get("category") or ""), "ongoing": ongoing,
            "count": max(1, _integer(n.get("count"), 1)), "active": active,
        })
    return sorted(cleaned, key=lambda n: int(n["time"]), reverse=True)[:20]


def format_notification_snapshot(result: dict) -> str:
    """Factual phone-check projection: posting time and evidence scope stay visible."""
    items = normalize_notifications(result)
    if not items:
        return "\n**通知栏近期通知**：本次结果没有有效的近期通知；不据此判断通话状态。"
    queried = datetime.now(ZoneInfo("Asia/Shanghai"))
    lines = [f"\n**通知栏近期通知**（{len(items)}个来源包；本次查询 {queried:%m-%d %H:%M}，保留原通知发布时间）:"]
    for n in items[:5]:
        posted = datetime.fromtimestamp(int(n["time"]) / 1000, ZoneInfo("Asia/Shanghai"))
        title, text = n["title"], n["text"]
        snippet = (title + (f": {text}" if text and text != title else "")).strip()[:50]
        scope = "本次查询仍存在" if n["active"] else "旧版缓存，是否仍存在未确认"
        kind = "；通话类别通知，不等于当前正在通话" if n["category"].lower() == "call" else ""
        lines.append(f"- {posted:%m-%d %H:%M} · {_display_name(n['package'])} · {scope}{kind}: {snippet}")
    return "\n".join(lines)


def _publish_notifications(point):
    """Update the current projection only; no trigger, DB mutation or LLM call."""
    from silicon_perception.sentinel import get_sentinel
    sentinel = get_sentinel()
    snapshot = getattr(sentinel, "_last_snapshot", None)
    if snapshot is not None:
        snapshot.notifications_summary = point.data.get("summary") or None
        if point.data.get("available") and point.captured_at:
            snapshot.field_ts["notifications"] = point.captured_at
        else:
            snapshot.field_ts.pop("notifications", None)


def handle_notification_relay_event(event: dict):
    """The socket receives an invalidation only; fetch content via existing relay.

    Never await the fetch in the socket receive loop (its reply uses that loop).
    """
    if event.get("type") == "notification_state_change" and _notifications_source:
        _notifications_source.request_refresh()

# 手机包名 → 可读名（2026-08-24：仿 AionsHome FOREGROUND_APP_TEXT，哨兵摘要用可读名而非包名）
_PHONE_APP_NAMES = {
    "com.tencent.mm": "微信",
    "com.tencent.mobileqq": "QQ",
    "com.tencent.wework": "企业微信",
    "com.xingin.xhs": "小红书",
    "com.ss.android.ugc.aweme": "抖音",
    "tv.danmaku.bili": "哔哩哔哩",
    "com.netease.cloudmusic": "网易云音乐",
    "com.kugou.android": "酷狗",
    "com.meituan": "美团",
    "com.sankuai.meituan.takeoutnew": "美团外卖",
    "com.meituan.takeoutnew": "美团外卖",
    "me.ele": "饿了么",
    "com.taobao.taobao": "淘宝",
    "com.jingdong.app.mall": "京东",
    "com.eg.android.AlipayGphone": "支付宝",
    "com.android.mms": "短信",
    "com.android.dialer": "电话",
    "com.google.android.dialer": "电话",
    "com.alibaba.android.rimet": "钉钉",
    "com.zhihu.android": "知乎",
    "com.sina.weibo": "微博",
}

# 通知 category → 优先级（2026-08-24：AionsHome NOTIFICATION_PRIORITY，越高越靠前）
_NOTIFICATION_PRIORITY = {
    "call": 100,
    "alarm": 95,
    "reminder": 90,
    "event": 85,
    "message": 80,
    "delivery": 80,
    "finance": 75,
    "system": 60,
}


def _display_name(pkg: str) -> str:
    return _PHONE_APP_NAMES.get(pkg, pkg)


def summarize_notifications(notifs) -> str:
    """把通知列表压成「可读名 × 数量」摘要（如 '微信×2, 美团外卖×1'）。

    2026-08-24 增强：按 category 优先级排序（call>alarm>reminder>message>delivery），
    用 relay 返回的 count 字段（手机端缓存按包名计数）。旧 APK 无 count 字段时回退 1。"""
    try:
        items = []
        for n in (notifs or []):
            if not isinstance(n, dict):
                continue
            pkg = str(n.get("package", "")).strip()
            if not pkg:
                continue
            count = 0
            try:
                count = int(n.get("count") or 1)
            except (TypeError, ValueError):
                count = 1
            cat = str(n.get("category", "") or "").lower()
            items.append({
                "pkg": pkg,
                "count": max(1, count),
                "priority": _NOTIFICATION_PRIORITY.get(cat, 40),
            })
        items.sort(key=lambda x: (-x["priority"], -x["count"], x["pkg"]))
        parts = [f"{_display_name(x['pkg'])}×{x['count']}" for x in items[:6]]
        return ", ".join(parts) if parts else ""
    except Exception:
        return ""


class NotificationsSource(DataSource):
    """通知变化即时刷新；每分钟轮询兜底，只读通知、零模型调用。"""

    name = "notifications"
    tick_interval = 1

    def __init__(self):
        self._last_notifs: list = []
        self._last_summary: str = ""
        self._last_count: int = 0
        self._consecutive_failures: int = 0
        self._last_success_time = None
        self._last_error = None
        self._available = False
        self._read_lock = asyncio.Lock()
        self._refresh_task = None
        self._dirty = False
        self._revision = 0
        global _notifications_source
        _notifications_source = self

    def request_refresh(self):
        self._revision += 1
        self._dirty = True
        self._available = False
        _publish_notifications(self._data_point(available=False))
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh(), name="notification-refresh")

    async def _refresh(self):
        while self._dirty:
            self._dirty = False
            point = await self.read(force=True)
            if not self._dirty:
                _publish_notifications(point)

    async def read(self, force=False) -> DataPoint:
        async with self._read_lock:
            if not force and self._available and self._last_success_time:
                age = (datetime.now() - datetime.fromisoformat(self._last_success_time)).total_seconds()
                if age < 60:
                    return self._data_point(available=True)
            revision = self._revision
            point = await self._poll()
            if revision != self._revision:
                # A removal arrived while this reply was in flight. The older
                # response cannot become current again; the refresh task refetches.
                self._available = False
                return self._data_point(available=False)
            self._available = bool(point.data.get("available"))
            return point

    def current_point(self) -> DataPoint:
        """Tick aggregation reads this after other sources finish awaiting.

        A previously collected DP cannot overwrite an intervening removal/event.
        """
        available = self._available and bool(self._last_success_time)
        if available:
            age = (datetime.now() - datetime.fromisoformat(self._last_success_time)).total_seconds()
            available = age <= 120
        return self._data_point(available=available)

    async def _poll(self) -> DataPoint:
        try:
            from shared_state import get_mobile_relay_callback
            relay = get_mobile_relay_callback()
            if not relay:
                self._consecutive_failures += 1
                self._last_error = "手机后台未连接"
                return self._data_point(available=False)

            import uuid
            result = await asyncio.wait_for(
                relay(str(uuid.uuid4()), "mobile_notifications", {}),
                timeout=8.0,
            )
            if not isinstance(result, dict) or not result.get("success"):
                self._consecutive_failures += 1
                failure = result if isinstance(result, dict) else {}
                self._last_error = str(
                    failure.get("error") or failure.get("content") or "通知读取失败")[:120]
                return self._data_point(available=False)

            if not isinstance(result.get("data"), list):
                raise ValueError("通知快照格式不可用")
            if result.get("snapshot_scope") == "active_notifications":
                observed = _integer(result.get("observed_at_ms"))
                age_ms = time.time() * 1000 - observed
                if observed <= 0 or not -60_000 <= age_ms <= 120_000:
                    raise ValueError("通知快照观测时间已过期或未提供")
            cleaned = normalize_notifications(result)
            self._last_notifs = cleaned
            self._last_summary = summarize_notifications(cleaned)
            self._last_count = len(cleaned)
            self._consecutive_failures = 0
            self._last_error = None
            self._last_success_time = (
                datetime.fromtimestamp(observed / 1000).isoformat()
                if result.get("snapshot_scope") == "active_notifications"
                else datetime.now().isoformat()
            )
            return self._data_point(available=True)
        except asyncio.TimeoutError:
            self._consecutive_failures += 1
            self._last_error = "通知读取超时"
            return self._data_point(available=False)
        except Exception as e:
            self._consecutive_failures += 1
            self._last_error = f"通知读取异常: {str(e)[:80]}"
            logger.debug(f"NotificationsSource 读取失败: {e}")
            return self._data_point(available=False)

    def _data_point(self, available: bool) -> DataPoint:
        return DataPoint(
            source=self.name,
            data={
                "available": available,
                "notifications": self._last_notifs if available else [],
                "summary": self._last_summary if available else "",
                "count": self._last_count if available else 0,
            },
            captured_at=self._last_success_time if available else None,
        )

    @property
    def connection_state(self) -> str:
        if self._consecutive_failures >= 3:
            return "failed"
        if self._available:
            return "connected"
        return "disconnected"

    @property
    def last_error(self):
        return self._last_error
