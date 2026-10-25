# 天气查询工具（当前已从 LLM 可见列表中移除——天气由 Sentinel WeatherSource 自动注入上下文）
# 保留此文件供未来可能需要手动查询非本地城市的场景重新启用。
# 2026-07-21 更新：使用 weatherCode + 结构化字段自建中文描述，替代不可靠的 lang_zh。

import httpx
from .base_tool import BaseTool, ToolResult, ToolStatus


class WeatherTool(BaseTool):
    """天气查询工具，使用 wttr.in 免费 API"""

    name = "get_weather"
    single_use = True  # 每轮对话只查一次天气，防止 Flash 重复提取导致多轮循环
    description = "查询指定城市的天气情况。当用户询问天气相关问题时使用。"

    def get_user_facing_description(self, **kwargs) -> str:
        city = kwargs.get("city", "")
        if city:
            return f"正在查询{city}的天气"
        return "正在查询天气"

    parameters_schema = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称，如'成都'、'上海'、'广州'"
            }
        },
        "required": ["city"]
    }

    async def execute(self, city: str) -> ToolResult:
        """查询天气"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"https://wttr.in/{city}?format=j1&lang=zh"
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

            current = data.get("current_condition", [{}])[0]

            # 提取结构化字段
            temp = current.get("temp_C", "?")
            feels_like = current.get("FeelsLikeC", "?")
            humidity = current.get("humidity", "?")
            code_raw = current.get("weatherCode")
            cloud_raw = current.get("cloudcover")
            uv_raw = current.get("uvIndex")
            wind_speed = current.get("windspeedKmph", "?")
            wind_dir = (current.get("winddir16Point") or "").strip()
            precip_raw = current.get("precipMM")
            vis_raw = current.get("visibility")

            # 用自建中文描述替代不可靠的 lang_zh
            try:
                from silicon_perception.collection.weather import _build_desc_zh
                desc = _build_desc_zh(
                    code=int(code_raw) if code_raw else None,
                    cloudcover=int(cloud_raw) if cloud_raw else None,
                    uv_index=int(uv_raw) if uv_raw else None,
                    wind_speed_kmh=int(wind_speed) if wind_speed and wind_speed != "?" else None,
                    precip_mm=float(precip_raw) if precip_raw else None,
                    visibility_km=int(vis_raw) if vis_raw else None,
                )
            except Exception:
                # 回退：wttr.in 自带描述
                desc = current.get("lang_zh", [{}])[0].get(
                    "value", current.get("weatherDesc", [{}])[0].get("value", "未知"))

            parts = [f"{city}当前天气：{desc}，温度{temp}°C（体感{feels_like}°C），湿度{humidity}%"]
            if wind_speed != "?":
                wind_str = f"风速{wind_speed}km/h"
                if wind_dir:
                    wind_str += f" {wind_dir}"
                parts.append(wind_str)
            if uv_raw:
                parts.append(f"紫外线{uv_raw}")
            if vis_raw:
                parts.append(f"能见度{vis_raw}km")

            return ToolResult(
                status=ToolStatus.SUCCESS,
                content="，".join(parts)
            )

        except httpx.HTTPError as e:
            return ToolResult(
                status=ToolStatus.ERROR,
                content="",
                error=f"天气查询失败：{str(e)}"
            )
        except Exception as e:
            return ToolResult(
                status=ToolStatus.ERROR,
                content="",
                error=f"天气查询出错：{str(e)}"
            )
