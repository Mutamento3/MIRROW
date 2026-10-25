"""健康追踪 (Health Tracker) API 路由 — 从 main.py 提取"""

from datetime import datetime
from fastapi import APIRouter

router = APIRouter(prefix="/api/health", tags=["health"])
from miband.router import router as miband_router
router.include_router(miband_router)


@router.get("")
async def health_overview():
    """用户健康数据聚合端点 — 融合哨兵状态 + 健康指标新鲜度。
    与 /monitor（debug 全量系统面板）定位不同：本端点面向用户，返回三大健康指标摘要。
    """
    import logging
    _log = logging.getLogger(__name__)

    # ── 哨兵状态 ──
    sentinel_info = {"running": False, "enabled": False, "tick_interval": 0}
    hr_info = {"connected": False, "last_reading": None, "last_time": None, "enabled": False, "error": None}
    try:
        from silicon_perception.sentinel import SENTINEL_AVAILABLE as _SA, get_sentinel
        if _SA:
            s = get_sentinel()
            if s is not None:
                st = s.status
                sentinel_info = {
                    "running": st.get("running", False),
                    "enabled": st.get("enabled", False),
                    "tick_interval": st.get("tick_interval", 0),
                }
                hr_info = {
                    "connected": st.get("hr_connection") == "connected",
                    "last_reading": st.get("hr_last_reading"),
                    "last_time": st.get("hr_last_reading_time"),
                    "enabled": st.get("hr_enabled", False),
                    "error": st.get("hr_error"),
                }
    except Exception as e:
        _log.warning(f"[health_overview] 哨兵状态获取失败: {e}")

    # ── 健康数据新鲜度 ──
    def _cache_summary(key: str) -> dict:
        try:
            from health_tracker.tracker import get_cache_or_stale
            data = get_cache_or_stale(key)
            raw = data.get("value")
            available = raw is not None
            result = {"available": available, "freshness": data.get("freshness", "未知"), "expired": data.get("expired", False)}
            if key == "steps" and available:
                # raw 可能是 dict（capture.py 格式）或 int（get_steps 直接写）
                if isinstance(raw, dict):
                    result["daily"] = raw.get("steps", raw.get("value", 0))
                else:
                    result["daily"] = raw
                # 外出差值
                from health_tracker.tracker import _cache
                od = _cache.get("outing_steps_diff", {})
                result["outing_diff"] = od.get("value", 0) if od else 0
            return result
        except Exception as e:
            _log.warning(f"[health_overview] 缓存读取失败 key={key}: {e}")
            return {"available": False, "error": str(e)}

    steps_info = _cache_summary("steps")
    sleep_info = _cache_summary("sleep")

    # ── 手机中继状态 ──
    phone_relay = False
    try:
        from shared_state import get_mobile_relay_callback
        _relay = get_mobile_relay_callback()
        phone_relay = _relay is not None
    except Exception:
        pass

    # ── 综合状态判定 ──
    degraded = []
    if not hr_info["connected"] and hr_info["enabled"]:
        degraded.append("心率未连接")
    if steps_info["expired"]:
        degraded.append("步数数据过期")
    overall = "degraded" if degraded else "healthy"

    return {
        "status": overall,
        "degraded_reasons": degraded or None,
        "sentinel": sentinel_info,
        "heart_rate": hr_info,
        "steps": steps_info,
        "sleep": sleep_info,
        "phone_relay": phone_relay,
    }


@router.get("/status-log")
async def get_health_status_log(date: str = ""):
    """获取指定日期的状态切换日志。"""
    from health_tracker.status_log import get_status_log
    log = get_status_log()
    active_date = date if date else datetime.now().strftime("%Y-%m-%d")
    entries = log.get_log(active_date)
    return {"date": active_date, "entries": entries, "count": len(entries)}


@router.get("/current-status")
async def get_health_current_status():
    """获取最近一次状态切换。"""
    from health_tracker.status_log import get_status_log
    log = get_status_log()
    current = log.get_current_status()
    return {"current": current}


@router.get("/cache/{key}")
async def get_health_cache(key: str):
    """查询健康数据缓存（steps/sleep/spo2/heart_rate）。"""
    from health_tracker.tracker import get_cache_or_stale, _cache
    data = get_cache_or_stale(key)
    raw = data["value"]
    # 适配 capture.py 返回的 dict 格式 {value, time, captured_at} vs 旧格式 number/string
    if isinstance(raw, dict):
        if key == "spo2" and "spo2" in raw:
            raw = raw["spo2"]
        elif key == "sleep" and "sleep" in raw:
            raw = raw["sleep"]
        elif key == "steps" and "steps" in raw:
            raw = raw["steps"]
        elif "data" in raw:
            raw = raw["data"]
        elif "value" in raw:
            raw = raw["value"]
    result = {"key": key, "data": raw, "freshness": data["freshness"], "expired": data["expired"]}
    # 步数附加外出差值
    if key == "steps":
        od = _cache.get("outing_steps_diff", {})
        result["outing_diff"] = od.get("value", 0) if od else 0
    return result


@router.post("/capture/{data_type}")
async def trigger_health_capture(data_type: str):
    """手动触发健康数据采集。data_type: sleep | spo2"""
    from health_tracker.tracker import get_health_tracker
    result = await get_health_tracker().trigger_manual_capture(data_type)
    return result


@router.get("/sync/pending")
async def get_pending_syncs():
    """获取未完成的同步记录。"""
    from health_tracker.sync_manager import get_sync_manager
    pending = get_sync_manager().get_pending()
    return {"pending": pending, "count": len(pending)}


@router.post("/sync/retry")
async def retry_health_sync(data_type: str = ""):
    """重试失败的同步。"""
    if not data_type:
        from health_tracker.sync_manager import get_sync_manager
        pending = get_sync_manager().get_pending_types()
        data_type = pending[0] if pending else ""
    if not data_type:
        return {"success": False, "error": "没有待重试的同步"}

    from health_tracker.tracker import get_health_tracker
    result = await get_health_tracker().trigger_manual_capture(data_type)
    return {"success": result.get("success", False), "data_type": data_type, "result": result}


# ── 每日健康汇总 ──────────────────────────────────────


@router.get("/daily")
async def get_daily_health(from_date: str = "", to_date: str = ""):
    """查询日期范围内的每日健康汇总。"""
    from silicon_perception.health_store import get_store
    from datetime import date as _date, timedelta as _td
    if not to_date:
        to_date = _date.today().isoformat()
    if not from_date:
        from_date = (_date.today() - _td(days=30)).isoformat()
    rows = get_store().get_daily_summaries(from_date, to_date)
    return {"from": from_date, "to": to_date, "count": len(rows), "data": rows}


# ── 睡眠数据手动上传 ──────────────────────────────

_SLEEP_GLM_PROMPT = """你是一个睡眠数据提取器。请从这张华为健康睡眠报告截图中提取夜间大睡眠数据。

⚠️ 重要：如果截图中有多条睡眠记录（如午睡/小憩 + 夜间睡眠），只提取时长最长的那条（即夜间大睡眠），忽略午睡/小憩。

⚠️ 截图上有两类数字，你必须区分它们：

【你要读的 — 数据摘要区（带时间单位的分钟数）】
- 特征：数字旁带 h/m 或 小时/分钟 等时间单位，数字通常 2-3 位数
- 示例：深睡 1h30m、浅睡 3h35m、总睡眠 6h35m
- 位置：通常在截图下半部分的文字列表区域

【你不要读的 — 睡眠阶段分布图（百分比）】
- 特征：小数字（1-100），通常不带时间单位，分布在彩色条状图上
- 示例：深睡 30%（这是占比！不是 30 分钟！）、浅睡 35%
- 位置：通常在截图中上部的图表区域

请按以下步骤操作：
1. 先在截图下半部分找到带「h/m」或「小时/分钟」的时长数字
2. 将每个时长转为分钟数：小时×60+分钟（如 1h30m=90, 6h35m=395, 6小时35分=395）
3. 输出以下 JSON（flat 格式，what_i_see 用中文描述你实际看到的原文）：

{
  "what_i_see": "用中文描述你在截图中实际看到的数字。格式：总睡眠XhYm，深睡XhYm，浅睡XhYm，REM XhYm，清醒XhYm，评分XX",
  "total_sleep_min": 总睡眠分钟数,
  "deep_sleep_min": 深睡分钟数,
  "shallow_sleep_min": 浅睡分钟数,
  "rem_sleep_min": 快速眼动/REM分钟数,
  "awake_min": 清醒分钟数,
  "score": 睡眠评分(0-100整数),
  "date": "报告日期 YYYY-MM-DD"
}

规则：
- 如果某字段在截图中找不到，设为 null
- what_i_see 必须如实描述你看到的原文数字和单位
- 只返回 JSON，不要任何解释
- 如果你只看到 1-100 的小数字（无时间单位），那是百分比段，不要用它们——total_sleep_min 设为 null"""


def _extract_json_object(text: str) -> str | None:
    """Extract the first complete JSON object from text (handles nested braces)."""
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


@router.post("/upload-sleep-image")
async def upload_sleep_image(body: dict):
    """上传睡眠截图，GLM-4V 视觉提取数据后写入缓存。Body: {image, date?}"""
    import base64, os, json, re, logging
    from health_tracker.tracker import get_health_tracker, put_cache
    from silicon_perception.health_store import get_store

    logger = logging.getLogger(__name__)
    image_b64 = body.get("image", "")
    date = body.get("date", datetime.now().strftime("%Y-%m-%d"))

    if not image_b64:
        return {"success": False, "error": "缺少 image 字段"}

    # 调用统一视觉入口
    try:
        from llm_client import call_vision_api
        from neuron_registry import neuron_trace
        with neuron_trace("health_capture_analyzer", model="GLM-4V") as trace:
            trace.set_input(_SLEEP_GLM_PROMPT[:300])
            result = await call_vision_api(image_b64, _SLEEP_GLM_PROMPT, mime_type="image/png")
            content = result.get("content", "")
            trace.set_output(content[:300] if content else "")
        if not content:
            return {"success": False, "error": "视觉分析返回空内容"}

        # 提取 JSON（处理嵌套括号）
        json_str = _extract_json_object(content)
        if not json_str:
            return {"success": False, "error": f"视觉分析返回非 JSON: {content[:200]}"}
        result = json.loads(json_str)

        # 记录 GLM 原始返回（便于排查）
        what_i_see = result.get("what_i_see", "")
        logger.info(f"Sleep GLM upload: what_i_see={what_i_see[:300]}")

        total = result.get("total_sleep_min")
        if not total:
            return {"success": False, "error": "未识别到睡眠数据，请确认上传的是睡眠报告截图。",
                    "glm_raw": content[:300], "what_i_see": what_i_see[:300]}

        # ── 校验逻辑（第 2 层：Python 交叉验证）──
        sub_keys = ["deep_sleep_min", "shallow_sleep_min", "rem_sleep_min", "awake_min"]
        subs = [result.get(k) for k in sub_keys]
        subs_sum = sum(v for v in subs if v is not None)
        subs_all_present = all(v is not None for v in subs)

        # 绝对下限：大睡眠至少 2 小时，< 120 分钟直接拒绝
        if total < 120:
            return {"success": False,
                    "error": f"识别到的睡眠时长仅 {total} 分钟（{total//60}h{total%60}m），不符合夜间大睡眠（至少 2 小时）。请确认上传的是夜间睡眠报告截图，而非午睡/小憩记录。",
                    "what_i_see": what_i_see[:300]}

        # 交叉验证（仅在子项齐全时有效）
        suspicious = False
        reason = None
        if subs_all_present:
            if total <= 180 and subs_sum <= 180:
                # 全部子项有值 + total 和 subs 都 ≤ 3h → 高概率百分比误读
                suspicious = True
                reason = f"数据偏短（仅 {total} 分钟），可能误读了睡眠阶段百分比。请核对截图是否包含「总睡眠时长 XhYm」的数据摘要区。"
                logger.warning(f"Sleep GLM: possible percentage misread — total={total} subs_sum={subs_sum} what_i_see={what_i_see[:200]}")
            elif total > 0 and subs_sum > 0:
                # 子项和应 ≈ total（允许 20% 偏差）
                diff_pct = abs(total - subs_sum) / max(total, 1)
                if diff_pct > 0.20:
                    suspicious = True
                    reason = f"睡眠各阶段之和（{subs_sum}min）与总时长（{total}min）不一致（偏差 {diff_pct:.0%}），数据可能不准确。"
                    logger.warning(f"Sleep GLM: cross-validation failed — total={total} subs_sum={subs_sum} diff={diff_pct:.2%}")
        # else: 子项不全，无法交叉验证，直接通过

        # ── 标准化字段名 + 写入缓存 ──
        sleep_data = {
            "total_sleep_min": total,
            "deep_sleep_min": result.get("deep_sleep_min"),
            "shallow_sleep_min": result.get("shallow_sleep_min") or result.get("light_sleep_min"),
            "rem_sleep_min": result.get("rem_sleep_min"),
            "awake_min": result.get("awake_min"),
            "score": result.get("score"),
            "captured_at": datetime.now().isoformat(),
            "source": "manual_upload",
        }
        # 无论 suspicious 与否都正常存储——标记交给前端决定是否提示
        put_cache("sleep", sleep_data)
        put_cache("sleep_data_status", {"status": "uploaded", "date": date})

        # 写入 health_snapshots 采集层（保留完整子项）
        try:
            get_store().insert_snapshot(
                sleep_data=json.dumps({
                    "total_sleep_min": sleep_data["total_sleep_min"],
                    "deep_sleep_min": sleep_data.get("deep_sleep_min"),
                    "shallow_sleep_min": sleep_data.get("shallow_sleep_min"),
                    "rem_sleep_min": sleep_data.get("rem_sleep_min"),
                    "awake_min": sleep_data.get("awake_min"),
                    "score": sleep_data.get("score"),
                    "source": "manual_upload",
                    "captured_at": sleep_data.get("captured_at"),
                }, ensure_ascii=False)
            )
        except Exception:
            pass

        # 写入 daily_health_summary 聚合层（含全部子项，L1 独立列）
        try:
            get_store().upsert_daily_summary(
                date,
                sleep_min=sleep_data["total_sleep_min"],
                deep_sleep_min=sleep_data.get("deep_sleep_min"),
                shallow_sleep_min=sleep_data.get("shallow_sleep_min"),
                rem_sleep_min=sleep_data.get("rem_sleep_min"),
                awake_min=sleep_data.get("awake_min"),
                sleep_score=sleep_data.get("score"),
            )
        except Exception:
            pass

        response = {"success": True, "data": sleep_data, "source": "manual_upload"}
        if suspicious:
            response["suspicious"] = True
            response["reason"] = reason
        return response

    except Exception as e:
        logger.warning(f"睡眠截图分析失败: {e}")
        return {"success": False, "error": f"分析失败: {str(e)[:200]}"}


@router.post("/sleep-data-status")
async def set_sleep_data_status(body: dict):
    """设置当日睡眠数据状态。Body: {status: "uploaded"|"skipped"}"""
    from health_tracker.tracker import put_cache
    status = body.get("status", "skipped")
    date = body.get("date", datetime.now().strftime("%Y-%m-%d"))
    put_cache("sleep_data_status", {"status": status, "date": date})
    return {"success": True, "status": status, "date": date}


@router.post("/steps/push")
async def push_steps(body: dict):
    """手机端推送步数。Body: {steps: int, source: str}。替代 relay 拉模式。"""
    from health_tracker.tracker import get_health_tracker, put_cache
    from silicon_perception.recording.health_store import get_store
    steps = body.get("steps", 0)
    today = datetime.now().strftime("%Y-%m-%d")
    ht = get_health_tracker()
    # 用手机推送的值直接写入（不再依赖 relay 拉取）
    ht._track_steps(steps)
    put_cache("steps", steps)
    get_store().upsert_daily_summary(today, steps=steps)
    return {"success": True, "steps": steps, "date": today}


@router.post("/gps/push")
async def push_gps(body: dict):
    """手机端推送 GPS。Body: {lat, lng, address?, category?}。替代 relay 拉模式。"""
    from silicon_perception.recording.health_store import get_store
    lat = body.get("lat")
    lng = body.get("lng")
    if lat is None or lng is None:
        return {"success": False, "error": "缺少 lat/lng"}
    get_store().insert_snapshot(
        location_lat=lat, location_lng=lng,
        location_address=body.get("address", ""),
        location_category=body.get("category", ""),
    )
    return {"success": True, "lat": lat, "lng": lng}


@router.get("/trends")
async def get_health_trends(days: int = 30):
    """健康趋势数据 + 行为基准摘要。供前端面板和监控台使用。"""
    from datetime import datetime, timedelta
    import sqlite3
    db_path = "silicon_perception/data/silicon_perception.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM daily_health_summary WHERE date >= ? ORDER BY date",
        (since,)
    ).fetchall()

    daily = []
    steps_vals, sleep_vals, hr_vals = [], [], []
    for r in rows:
        daily.append({
            "date": r["date"], "steps": r["steps"], "sleep_min": r["sleep_min"],
            "hr_min": r["hr_min"], "hr_max": r["hr_max"], "hr_avg": r["hr_avg"],
            "outing_steps": r["outing_steps"], "avg_steps_7d": r["avg_steps_7d"],
        })
        if r["steps"]: steps_vals.append(r["steps"])
        if r["sleep_min"]: sleep_vals.append(r["sleep_min"])

    # 心率7天趋势
    hr_rows = conn.execute(
        "SELECT AVG(heart_rate) as avg_hr, date(timestamp) as d FROM health_snapshots "
        "WHERE heart_rate IS NOT NULL AND timestamp >= ? GROUP BY d ORDER BY d",
        ((datetime.now() - timedelta(days=7)).isoformat(),)
    ).fetchall()
    for r in hr_rows:
        if r["avg_hr"]:
            hr_vals.append({"date": r["d"], "avg_hr": round(r["avg_hr"])})

    conn.close()

    # 行为基准
    baseline = {}
    try:
        from silicon_perception.analysis.behavior_profile import get_behavior_profile
        bp = get_behavior_profile()
        baseline = bp.get_debug_view()
    except Exception:
        pass

    return {
        "daily": daily,
        "hr_trend_7d": hr_vals,
        "summary": {
            "steps_avg_30d": round(sum(steps_vals) / len(steps_vals)) if steps_vals else 0,
            "sleep_avg_30d": round(sum(sleep_vals) / len(sleep_vals)) if sleep_vals else 0,
            "days_with_data": len(daily),
        },
        "behavior_baseline": baseline,
    }


# ── 行为基准 API（Phase 4）──────────────────────────


@router.get("/baseline")
async def get_behavior_baseline():
    """获取全量行为基线：置信度 + 7维度 stats + 趋势方向 + 漂移状态。"""
    try:
        from silicon_perception.analysis.behavior_profile import get_behavior_profile
        bp = get_behavior_profile()
        debug = bp.get_debug_view()

        # 趋势信号
        trends = {}
        try:
            from silicon_perception.analysis.trend_calculator import get_trend_signals
            trends = get_trend_signals()
        except Exception:
            pass

        # 漂移状态
        drift = bp._cache.get("_drift", {})

        return {
            "confidence": debug.get("confidence", 0),
            "cold_start": debug.get("confidence", 0) < 0.7,
            "dimensions": debug.get("dimensions", {}),
            "trend_signals": trends,
            "drift": drift,
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"基线查询失败: {e}")
        return {"confidence": 0, "cold_start": True, "dimensions": {}, "trend_signals": {}, "drift": {}}


@router.get("/baseline/trends")
async def get_behavior_trends():
    """获取趋势数据：每维度 slope/R²/方向/强度。"""
    try:
        from silicon_perception.analysis.trend_calculator import _trend_cache
        return {"trends": _trend_cache}
    except Exception:
        return {"trends": {}}


@router.get("/baseline/deviations")
async def get_baseline_deviations(days: int = 7):
    """获取偏离时间线：时间/维度/z_score/严重度/复合信号。"""
    import sqlite3
    from datetime import datetime, timedelta

    try:
        db_path = "silicon_perception/data/silicon_perception.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT * FROM baseline_deviations WHERE created_at >= ? ORDER BY timestamp DESC LIMIT 200",
            (since,)
        ).fetchall()
        conn.close()

        deviations = []
        for r in rows:
            deviations.append({
                "timestamp": r["timestamp"],
                "dimension": r["dimension"],
                "current_value": r["current_value"],
                "baseline_mean": r["baseline_mean"],
                "z_score": r["z_score"],
                "severity": r["severity"],
                "deviation_direction": r["deviation_direction"],
                "compound_group_id": r["compound_group_id"],
                "first_seen_at": r["first_seen_at"],
                "repeat_count": r["repeat_count"],
                "last_seen_at": r["last_seen_at"],
            })

        # 按 compound_group_id 分组的复合信号摘要
        from collections import defaultdict
        groups = defaultdict(list)
        for d in deviations:
            gid = d.get("compound_group_id")
            if gid:
                groups[gid].append(d["dimension"])

        return {
            "days": days,
            "count": len(deviations),
            "deviations": deviations,
            "compound_groups": {gid: dims for gid, dims in groups.items() if len(dims) >= 2},
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"偏离查询失败: {e}")
        return {"days": days, "count": 0, "deviations": [], "compound_groups": {}}


@router.get("/baseline/drift")
async def get_baseline_drift():
    """获取基线漂移状态。"""
    try:
        from silicon_perception.analysis.behavior_profile import get_behavior_profile
        bp = get_behavior_profile()
        return {"drift": bp._cache.get("_drift", {})}
    except Exception:
        return {"drift": {}}
