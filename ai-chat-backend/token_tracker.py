"""
Token 消耗追踪模块

非阻塞记录每次 LLM 调用的 token 消耗到日志文件。
所有写入通过 asyncio.create_task() 触发，不影响主流程。

2026-08 降本改造：额外记录 prompt 缓存命中/未命中拆分，并提供价格换算
（DeepSeek 官方定价，缓存命中、未命中与输出分别按峰谷价格计费）。
"""
import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from deepseek_tariff import is_peak as _is_peak
from llm_usage import extract_token_usage

logger = logging.getLogger(__name__)

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_log_path = os.path.join(_log_dir, "token_usage.log")
_write_lock = asyncio.Lock()


# ── 价格表（元/百万 tokens），平峰/高峰两套 ──
# 来源：DeepSeek 官方价格页，2026-10-08 核实；历史日志 cost 不回写。
# 峰值时段：北京时间周一至周五 9–12 / 14–18，周末及中国节假日全天平峰。
PRICE_TABLE: Dict[str, Dict[str, Dict[str, float]]] = {
    "pro": {
        "cache_hit": {"off": 0.15, "peak": 0.30},
        "cache_miss": {"off": 4.5, "peak": 9.0},
        "output": {"off": 13.5, "peak": 27.0},
    },
    "flash": {
        "cache_hit": {"off": 0.02, "peak": 0.04},
        "cache_miss": {"off": 1.0, "peak": 2.0},
        "output": {"off": 4.0, "peak": 8.0},
    },
}


def _model_tier(model: str) -> Optional[str]:
    """按模型名归类到价格档。glm/gemini 无定价数据返回 None。"""
    m = (model or "").lower()
    if "flash" in m:
        return "flash"
    if "glm" in m or "gemini" in m:
        return None
    # deepseek-v4-pro 及未识别模型默认按 pro 档（保守估计）
    return "pro"


def estimate_cost_yuan(
    model: str,
    cache_hit: int = 0,
    cache_miss: int = 0,
    completion: int = 0,
    ts_str: str = "",
    provider: str = "",
) -> Optional[float]:
    """估算官方成本；非 DS、无有效时间或未知假期年份的高峰时段返回 None。"""
    # PRICE_TABLE 是 DeepSeek 官方分时价格。其它 provider 即使提供同名
    # 模型也不能套用该价格，保留 None 交给账单或后续 provider 价表核算。
    if provider and provider != "deepseek":
        return None
    tier = _model_tier(model)
    if not tier or tier not in PRICE_TABLE:
        return 0.0
    peak = _is_peak(ts_str)
    if peak is None:
        return None
    key = "peak" if peak else "off"
    p = PRICE_TABLE[tier]
    cost = (
        max(0, cache_hit or 0) * p["cache_hit"][key]
        + max(0, cache_miss or 0) * p["cache_miss"][key]
        + max(0, completion or 0) * p["output"][key]
    ) / 1_000_000
    return cost


async def log_token_usage(caller: str, model: str, usage: Any = None, *, provider: str = "deepseek",
                          provider_variant: str = "", request_id: str = ""):
    """异步写入 token 消耗记录。

    调用方应使用 asyncio.create_task() 触发，不阻塞响应。

    Args:
        caller: 调用方标识 (e.g. "chat_final_reply", "agent_tools", "wander_message")
        model: 模型名称
        usage: token 用量数据。支持 dict (httpx) 或 openai.types.CompletionUsage 对象。
               为 None 时仍记录一行（标记为 unavailable）。
    """
    try:
        values = extract_token_usage(usage)
        prompt, completion, total, cache_hit, cache_miss = (
            values[key] if values[key] is not None else -1
            for key in ("prompt", "completion", "total", "cache_hit", "cache_miss")
        )

        ts = datetime.now().isoformat()
        record = {
            "ts": ts,
            "caller": caller,
            "provider": provider,
            "model": model,
            "prompt": prompt,
            "completion": completion,
            "total": total,
            "cache_hit": cache_hit,
            "cache_miss": cache_miss,
            "cost": estimate_cost_yuan(
                model, cache_hit, cache_miss, completion, ts, provider=provider,
            ) if min(cache_hit, cache_miss, completion) >= 0 else None,
        }

        if provider_variant:
            record["provider_variant"] = provider_variant
        if request_id:
            record["request_id"] = request_id
        for key, field in (('cache_write_tokens', 'cache_write'),
                           ('cache_write_5m_tokens', 'cache_write_5m'),
                           ('cache_write_1h_tokens', 'cache_write_1h'),
                           ('reasoning_tokens', 'reasoning')):
            if values[field] is not None:
                record[key] = values[field]
        from llm_usage_scope import usage_metadata
        record.update(usage_metadata())
        line = json.dumps(record, ensure_ascii=False) + "\n"

        async with _write_lock:
            os.makedirs(_log_dir, exist_ok=True)
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:
        logger.warning(f"Token usage logging failed: {e}")
