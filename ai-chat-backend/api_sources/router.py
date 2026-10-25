"""Settings and bounded metadata-only diagnostics for the local MIRROW app."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from . import audit
from .config import ConfigConflict, ConfigError, RoutingConfig, public_config, save_config

class SafeConfigRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                raise HTTPException(422, detail="配置字段不正确，请检查站点地址、模型名、适配选项和密钥长度") from None
        return handler


router = APIRouter(prefix="/api/api-sources", tags=["api-sources"], route_class=SafeConfigRoute)


class SaveRequest(RoutingConfig):
    revision: str


@router.get("/config")
async def get_config():
    try:
        return await asyncio.to_thread(public_config)
    except ConfigError as exc:
        raise HTTPException(422, detail=str(exc)) from None


@router.put("/config")
async def put_config(request: SaveRequest):
    try:
        config = RoutingConfig.model_validate(request.model_dump(exclude={"revision"}))
        # model_dump intentionally excludes write-only keys; keep them only for
        # this backend-local persistence operation.
        for station, original in zip(config.stations, request.stations):
            station.api_key = original.api_key
        return await asyncio.to_thread(save_config, config, request.revision)
    except ConfigConflict as exc:
        raise HTTPException(409, detail=str(exc)) from None
    except ConfigError as exc:
        raise HTTPException(422, detail=str(exc)) from None


@router.post("/test/{source_id}")
async def test_source(source_id: str):
    # Test only a saved source, with a synthetic instruction, never chat context.
    from .config import load_config
    from .runner import run_structured
    try:
        config, env, _ = await asyncio.to_thread(load_config)
    except ConfigError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    for station in config.stations:
        for model in station.models:
            if model.id == source_id:
                if not station.enabled or not model.enabled or not env.get(station.key_env):
                    raise HTTPException(400, detail="来源已停用或未配置密钥")
                source = {"station": station.model_dump(), "model": model.model_dump(), "api_key": env[station.key_env], "ordinal": 1, "availability_managed": True}
                # An explicit paid test may verify recovery before cooldown ends.
                source['stable'] = True
                result = await run_structured([{"role": "system", "content": "你是接口连通性测试助手。"}, {"role": "user", "content": '仅返回 {"ok":true}。'}],
                                              temperature=0, max_tokens=1024, route="connection_test", enable_thinking=None,
                                              sources=[source], timeout_seconds=30,
                                              result_validator=lambda item: None if item.parsed == {"ok": True} else "test_contract_failed")
                return {"status": result.status, "error_code": result.error, "http_status": result.http_status,
                        "duration_ms": result.duration_ms, "task_id": result.task_id}
    raise HTTPException(404, detail="来源不存在")


@router.get("/calls")
async def get_calls(days: int = Query(1, ge=1, le=365), date: str = "", category: str = "", node: str = "", status: str = "", station_id: str = "", page: int = Query(0, ge=0, le=10000)):
    since = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)).isoformat()
    until = ""
    if date:
        try:
            start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=8)))
            since, until = start.isoformat(), (start+timedelta(days=1)).isoformat()
        except ValueError:
            raise HTTPException(422, detail="日期需为 YYYY-MM-DD") from None
    return await asyncio.to_thread(audit.snapshot, since=since, until=until, category=category, node=node, status=status, station_id=station_id, page=page)


@router.get("/health")
async def get_health():
    from .config import load_config
    from .health import snapshot
    try:
        config, environment, _ = await asyncio.to_thread(load_config)
        return await asyncio.to_thread(snapshot, config, environment)
    except ConfigError as exc:
        raise HTTPException(422, detail=str(exc)) from None
