"""天气数据源 — wttr.in 免费 API，可配置刷新间隔。

城市可配置：环境变量 WEATHER_CITY → settings.json silicon_perception.weather.city → 空。
刷新间隔：settings.json silicon_perception.weather.refresh_hours → 默认 2 小时。
数据仅作 L1 聚合层原料（上下文注入），不参与 trigger / anomaly_detector / baseline。

2026-07-21 重构：不再依赖 wttr.in 不可靠的 lang_zh 翻译字段，
改用 weatherCode（WWO 数值码，稳定可靠）做主分类，
cloudcover / precipMM / windspeedKmph / uvIndex / visibility 补充强度和交叉现象，
自建中文复合描述。
"""

import os
import asyncio
import logging
import math
from datetime import datetime
from typing import Optional

from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)


# ── WWO weatherCode → 中文基础描述 ───────────────────
# https://www.worldweatheronline.com/weather-api/api/docs/weather-icons.aspx
_WEATHER_CODE_ZH: dict[int, str] = {
    # 晴/云
    113: "晴",
    116: "少云",
    119: "多云",
    122: "阴",
    # 雾/霾
    143: "薄雾",
    248: "雾",
    260: "冻雾",
    # 雨 — 小雨系
    176: "小雨",   # Patchy rain possible
    263: "小雨",   # Patchy light drizzle
    266: "小雨",   # Light rain
    293: "小雨",   # Patchy light rain
    296: "小雨",   # Light rain
    353: "阵雨",   # Light rain shower
    # 雨 — 中雨系
    299: "中雨",   # Moderate rain at times
    302: "中雨",   # Moderate rain
    356: "中雨",   # Moderate or heavy rain shower
    # 雨 — 大雨系
    305: "大雨",   # Heavy rain at times
    308: "大雨",   # Heavy rain
    359: "暴雨",   # Torrential rain shower
    # 冻雨
    185: "冻雨",
    281: "冻雨",
    284: "冻雨",
    311: "冻雨",
    314: "冻雨",
    # 雨夹雪
    182: "雨夹雪",
    317: "雨夹雪",
    320: "雨夹雪",
    362: "雨夹雪",
    365: "雨夹雪",
    # 雪
    179: "小雪",
    323: "小雪",
    326: "小雪",
    329: "小雪",
    332: "中雪",
    335: "中雪",
    338: "大雪",
    371: "大雪",
    368: "阵雪",
    # 暴风雪
    227: "暴风雪",
    230: "暴风雪",
    # 冰粒
    350: "冰粒",
    374: "冰粒",
    377: "冰粒",
    # 雷
    200: "雷阵雨",
    386: "雷阵雨",
    389: "雷阵雨",
    392: "雷阵雪",
    395: "雷阵雪",
}


def _build_desc_zh(
    code: Optional[int],
    cloudcover: Optional[int],
    uv_index: Optional[int],
    wind_speed_kmh: Optional[int],
    precip_mm: Optional[float],
    visibility_km: Optional[int],
) -> str:
    """用 WWO weatherCode + 结构化字段自建中文天气描述。

    规则：
    1. weatherCode → 基础描述（查表，未知码回退 cloudcover 推断）
    2. 晴天 + UV≥8 → 追加"紫外线强"
    3. 风速 ≥30 km/h → 追加"大风"（风雨合并为"风雨交加"）
    4. 晴天 + visibility<5km → 追加"有霾"（修正 WWO 的 "Smoky haze" 偏差）
    5. precipMM >0 但 code 不是雨类 → 追加降水量
    """
    # 1. 基础描述
    if code is not None and code in _WEATHER_CODE_ZH:
        base = _WEATHER_CODE_ZH[code]
    elif cloudcover is not None:
        # 回退：按云量推断
        if cloudcover <= 25:
            base = "晴"
        elif cloudcover <= 60:
            base = "少云"
        elif cloudcover <= 90:
            base = "多云"
        else:
            base = "阴"
    else:
        base = ""

    if not base:
        return ""

    parts: list[str] = [base]

    # 2. 紫外线强度（晴天/少云时关注）
    _is_sunny = code in (113, 116) if code is not None else (cloudcover is not None and cloudcover <= 25)
    if _is_sunny and uv_index is not None:
        if uv_index >= 8:
            parts.append("紫外线强")
        elif uv_index <= 2:
            parts.append("紫外线弱")

    # 3. 风速
    _is_rain = base in ("小雨", "中雨", "大雨", "暴雨", "阵雨", "雷阵雨", "冻雨", "雨夹雪")
    if wind_speed_kmh is not None and wind_speed_kmh >= 30:
        if _is_rain:
            parts.append("大风")  # 风雨交加 → "中雨，大风"
        else:
            parts.append("大风")

    # 4. 霾修正：晴/少云 但能见度低
    if _is_sunny and visibility_km is not None and visibility_km < 5:
        parts.append("有霾")

    # 5. 降水量（非雨类码但有降水时）
    if not _is_rain and precip_mm is not None and precip_mm > 0:
        if precip_mm <= 2.4:
            parts.append("微量降水")
        else:
            parts.append(f"降水{precip_mm:.0f}mm")

    return "，".join(parts)


def _get_city() -> Optional[str]:
    """天气监控城市：env → settings → None（开源默认不填）。

    仿 gps.py:_get_api_key() 的降级范式。返回空表示未配置，
    调用方应跳过采集而非拼坏 URL。
    """
    city = os.getenv("WEATHER_CITY")
    if city and city.strip():
        return city.strip()
    try:
        from settings_manager import get_setting
        ss = get_setting("silicon_perception") or {}
        city = (ss.get("weather", {}) or {}).get("city")
        if city and str(city).strip():
            return str(city).strip()
    except Exception:
        pass
    return None


class WeatherSource(DataSource):
    """天气 — wttr.in，一天一次"""

    name = "weather"

    def __init__(self):
        self._temp: Optional[int] = None
        self._humidity: Optional[int] = None
        self._desc: Optional[str] = None
        self._code: Optional[int] = None
        self._cloudcover: Optional[int] = None
        self._uv_index: Optional[int] = None
        self._wind_speed: Optional[int] = None
        self._wind_dir: Optional[str] = None
        self._precip_mm: Optional[float] = None
        self._visibility: Optional[int] = None
        self._last_fetch_time: Optional[datetime] = None
        self._last_success_date: Optional[str] = None
        self._last_error: Optional[str] = None
        self._provider: Optional[str] = None
        self._observed_city: Optional[str] = None
        self._consecutive_failures: int = 0
        self._refresh_seconds: int = 7200  # 默认 2 小时
        # 从 settings 读取刷新间隔
        try:
            from settings_manager import get_setting
            cfg = get_setting("silicon_perception") or {}
            weather_cfg = cfg.get("weather", {}) or {}
            if isinstance(weather_cfg, dict) and "refresh_hours" in weather_cfg:
                v = weather_cfg["refresh_hours"]
                if isinstance(v, (int, float)) and v >= 0.1:
                    self._refresh_seconds = int(v * 3600)
        except Exception:
            pass
        # 从 daily_health_summary 恢复上次天气（重启恢复）
        # 注意：schema migration 在此 init 之后才运行，新列可能尚不存在，
        # 故只用旧三列 + date 做基础恢复，新字段留到下次 read() 补全。
        try:
            from silicon_perception.recording.health_store import get_store
            store = get_store()
            conn = store._get_conn()
            row = conn.execute(
                "SELECT weather_temp, weather_humidity, weather_desc, date "
                "FROM daily_health_summary WHERE weather_temp IS NOT NULL "
                "ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row:
                self._temp = row["weather_temp"]
                self._humidity = row["weather_humidity"]
                self._desc = row["weather_desc"]
                self._last_success_date = row["date"]
                # 将日期字符串解析为 datetime（取当天 00:00，保守估计上次采集时间）
                try:
                    self._last_fetch_time = datetime.fromisoformat(row["date"] + "T00:00:00")
                except Exception:
                    pass
                logger.info(f"WeatherSource: 从 DB 恢复天气 {self._desc} {self._temp}°C {self._humidity}%")
        except Exception:
            pass

    def current_observation(self, *, now=None):
        """Narrow read-only projection of a real cached observation, never fetch."""
        stamp = self._last_fetch_time
        city = self._observed_city
        if (not city or city != _get_city() or self._provider not in {'wttr.in', 'amap'}
                or not isinstance(stamp, datetime) or isinstance(self._temp, bool)
                or not isinstance(self._temp, (int, float)) or not math.isfinite(self._temp)
                or not -100 <= self._temp <= 70):
            return None
        stamp = stamp.astimezone()
        age = ((now or datetime.now().astimezone()) - stamp).total_seconds()
        if not 0 <= age <= 21600:
            return None
        return dict(temperature_c=self._temp, city=city, provider=self._provider,
                    as_of=stamp.isoformat(), refresh_failed=bool(self._last_error))

    @property
    def tick_interval(self) -> int:
        """tick_interval 是"每 N 个基础 tick（60s）采集一次"，不是秒数。

        返回 5 = 每 5 分钟检查一次；read() 内部按时间差判断是否需要重新采集，
        未到刷新间隔则返缓存——实际外部请求频率由 refresh_hours 控制。
        """
        return 1 if self._last_fetch_time is None else 5

    async def read(self) -> DataPoint:
        now = datetime.now()
        # 时间门控：距上次成功采集不到 refresh_seconds 秒，返回缓存
        if self._last_fetch_time is not None:
            elapsed_sec = (now - self._last_fetch_time).total_seconds()
            if elapsed_sec < self._refresh_seconds:
                return DataPoint(source=self.name, data={
                "weather_temp": self._temp,
                "weather_humidity": self._humidity,
                "weather_desc": self._desc,
                "weather_code": self._code,
                "weather_cloudcover": self._cloudcover,
                "weather_uv_index": self._uv_index,
                "weather_wind_speed": self._wind_speed,
                "weather_wind_dir": self._wind_dir,
                "weather_precip_mm": self._precip_mm,
                "weather_visibility": self._visibility,
            }, captured_at=self._last_fetch_time.isoformat())

        # 主源 wttr.in；失败后用项目已有的高德 Web 服务 key 做城市实况兜底。
        try:
            city = _get_city()
            if not city:
                # 未配置城市：跳过采集，不拼坏 URL
                logger.info("WeatherSource: 未配置监控城市（WEATHER_CITY / settings.weather.city），跳过采集")
                self._last_error = "未配置监控城市"
                return DataPoint(source=self.name, data={
                    "weather_temp": self._temp,
                    "weather_humidity": self._humidity,
                    "weather_desc": self._desc,
                }, captured_at=self._last_fetch_time.isoformat() if self._last_fetch_time else None)
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                primary_error = None
                try:
                    resp = await client.get(f"https://wttr.in/{city}?format=j1&lang=zh")
                    resp.raise_for_status()
                    current = resp.json().get("current_condition", [{}])[0]
                    if not current or not current.get("temp_C"):
                        raise ValueError("wttr.in 返回缺少实况温度")
                    self._apply_wttr(current)
                    self._provider = "wttr.in"
                except Exception as exc:
                    primary_error = exc
                    amap = await self._fetch_amap_fallback(client, city)
                    if not amap:
                        raise RuntimeError(f"主源失败且高德兜底不可用: {str(primary_error)[:100]}")
                    self._apply_amap(amap)
                    self._provider = "amap"

            self._last_fetch_time = now
            self._observed_city = city
            self._last_success_date = now.strftime("%Y-%m-%d")
            self._last_error = None
            self._consecutive_failures = 0

            # 写入 L1
            try:
                from silicon_perception.recording.health_store import get_store
                get_store().upsert_daily_summary(
                    self._last_success_date,
                    weather_temp=self._temp,
                    weather_humidity=self._humidity,
                    weather_desc=self._desc,
                    weather_code=self._code,
                    weather_cloudcover=self._cloudcover,
                    weather_uv_index=self._uv_index,
                    weather_wind_speed=self._wind_speed,
                    weather_wind_dir=self._wind_dir,
                    weather_precip_mm=self._precip_mm,
                    weather_visibility=self._visibility,
                )
            except Exception as exc:
                # 采集事实仍可用于本轮，但持久化失败必须可见，不能伪装成完整成功。
                self._last_error = f"天气写库失败: {str(exc)[:100]}"
                logger.warning("WeatherSource: 天气写库失败: %s", exc)

            logger.info(
                "WeatherSource: %s %s %s°C %s%% (provider=%s)",
                city, self._desc, self._temp, self._humidity, self._provider,
            )
            return DataPoint(source=self.name, data={
                "weather_temp": self._temp,
                "weather_humidity": self._humidity,
                "weather_desc": self._desc,
                "weather_code": self._code,
                "weather_cloudcover": self._cloudcover,
                "weather_uv_index": self._uv_index,
                "weather_wind_speed": self._wind_speed,
                "weather_wind_dir": self._wind_dir,
                "weather_precip_mm": self._precip_mm,
                "weather_visibility": self._visibility,
            }, captured_at=self._last_fetch_time.isoformat())

        except Exception as e:
            self._consecutive_failures += 1
            self._last_error = f"天气获取失败: {str(e)[:120]}"
            logger.info("WeatherSource: 获取失败 (%s)，%s", e, "保留上次值" if self._temp is not None else "无缓存")
            return DataPoint(source=self.name, data={
                "weather_temp": self._temp,
                "weather_humidity": self._humidity,
                "weather_desc": self._desc,
            }, captured_at=self._last_fetch_time.isoformat() if self._last_fetch_time else None)

    def _apply_wttr(self, current: dict) -> None:
        self._temp = int(current["temp_C"]) if current.get("temp_C") else None
        self._humidity = int(current["humidity"]) if current.get("humidity") else None
        self._code = int(current["weatherCode"]) if current.get("weatherCode") else None
        self._cloudcover = int(current["cloudcover"]) if current.get("cloudcover") else None
        self._uv_index = int(current["uvIndex"]) if current.get("uvIndex") else None
        self._wind_speed = int(current["windspeedKmph"]) if current.get("windspeedKmph") else None
        self._wind_dir = (current.get("winddir16Point") or "").strip() or None
        self._precip_mm = float(current["precipMM"]) if current.get("precipMM") else None
        self._visibility = int(current["visibility"]) if current.get("visibility") else None
        self._desc = _build_desc_zh(
            code=self._code,
            cloudcover=self._cloudcover,
            uv_index=self._uv_index,
            wind_speed_kmh=self._wind_speed,
            precip_mm=self._precip_mm,
            visibility_km=self._visibility,
        )

    async def _fetch_amap_fallback(self, client, city: str) -> Optional[dict]:
        """Resolve city to adcode, then fetch AMap live weather without exposing the key."""
        try:
            key = os.getenv("AMAP_API_KEY")
            if not key:
                try:
                    from settings_manager import get_setting
                    perception = get_setting("silicon_perception") or {}
                    key = (perception.get("gps") or {}).get("amap_api_key")
                except Exception:
                    key = None
            if not key:
                return None
            geo_resp = await client.get(
                "https://restapi.amap.com/v3/geocode/geo",
                params={"address": city, "city": city, "key": key},
            )
            geo_resp.raise_for_status()
            geo = geo_resp.json()
            geocodes = geo.get("geocodes") or []
            adcode = geocodes[0].get("adcode") if geocodes else None
            if geo.get("status") != "1" or not adcode:
                return None
            weather_resp = await client.get(
                "https://restapi.amap.com/v3/weather/weatherInfo",
                params={"city": adcode, "extensions": "base", "key": key},
            )
            weather_resp.raise_for_status()
            weather = weather_resp.json()
            lives = weather.get("lives") or []
            if weather.get("status") != "1" or not lives:
                return None
            return lives[0]
        except Exception as exc:
            logger.debug("WeatherSource: 高德兜底失败: %s", exc)
            return None

    def _apply_amap(self, live: dict) -> None:
        self._temp = int(float(live["temperature"])) if live.get("temperature") not in (None, "") else None
        self._humidity = int(float(live["humidity"])) if live.get("humidity") not in (None, "") else None
        self._desc = (live.get("weather") or "").strip() or "天气状况未知"
        self._wind_dir = (live.get("winddirection") or "").strip() or None
        # 高德实况没有与 wttr 对等的这些字段，清空而不是沿用旧供应商的数据。
        self._code = None
        self._cloudcover = None
        self._uv_index = None
        self._wind_speed = None
        self._precip_mm = None
        self._visibility = None

    @property
    def connection_state(self) -> str:
        if self._consecutive_failures >= 3:
            return "failed"
        if self._temp is not None and self._last_success_date == datetime.now().strftime("%Y-%m-%d"):
            return "connected"
        return "disconnected"

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_success_time(self) -> Optional[datetime]:
        return self._last_fetch_time

    def context_description(self) -> str:
        """Objective context text with explicit freshness/failure semantics."""
        if self._temp is None or not self._desc:
            return "天气数据暂不可用。"
        now = datetime.now()
        age_seconds = (now - self._last_fetch_time).total_seconds() if self._last_fetch_time else None
        same_day = self._last_success_date == now.strftime("%Y-%m-%d")
        fact = f"{self._desc} {self._temp}°C 湿度{self._humidity}%"
        if same_day and age_seconds is not None and age_seconds <= max(self._refresh_seconds * 2, 21600):
            if self._consecutive_failures > 0:
                return f"今日天气上次确认：{fact}；当前刷新暂时失败。"
            return f"今日天气：{fact}。"
        if self._last_success_date:
            return f"天气当前不可确认；上次有效记录（{self._last_success_date}）为：{fact}。"
        return "天气数据暂不可用。"

    @property
    def today_weather(self) -> Optional[dict]:
        """供外部读取今日天气。"""
        if self._temp is None or self._last_success_date != datetime.now().strftime("%Y-%m-%d"):
            return None
        return {
            "temp": self._temp,
            "humidity": self._humidity,
            "desc": self._desc,
            "code": self._code,
            "cloudcover": self._cloudcover,
            "uv_index": self._uv_index,
            "wind_speed": self._wind_speed,
            "wind_dir": self._wind_dir,
            "precip_mm": self._precip_mm,
            "visibility": self._visibility,
        }
