"""Private Memory V2 library and explicitly separate read-only old archive."""
import asyncio
from fastapi import APIRouter, HTTPException, Query, Request, Response
from context_inspection import redact, require_private_inspection
from memory_v2 import library

router = APIRouter(tags=["memory-library"])


async def _read(request, response, operation, *args, **kwargs):
    require_private_inspection(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return redact(await asyncio.to_thread(operation, *args, **kwargs))
    except KeyError:
        raise HTTPException(404, "条目已变化或不存在，请刷新列表") from None
    except library.SourceChanged:
        raise HTTPException(409, "原文来源已变化或暂不可核验，请刷新后重试") from None
    except Exception:
        raise HTTPException(503, "记忆读取暂未就绪，请稍后重试") from None


@router.post("/memory/hold")
@router.post("/memory/upload")
@router.post("/profile/questionnaire")
async def retired_write():
    raise HTTPException(410, "旧记忆写入已退役；资料可通过聊天附件或长文交给 AI，旧档案只读")


@router.get("/memory/breath")
async def search_memory(request: Request, response: Response, query: str = ""):
    from memory_v2.retrieval import search_records
    require_private_inspection(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return redact({"memories": await search_records(query), "source": "Memory V2"})
    except Exception:
        raise HTTPException(503, "Memory V2 当前未就绪") from None


@router.get("/api/memory-v2/library/status")
@router.get("/memory/pulse")
async def memory_status(request: Request, response: Response):
    return await _read(request, response, library.library_status)


@router.get('/api/memory-v2/library/continuation-progress')
async def continuation_progress(request: Request, response: Response):
    from memory_v2.continuation_status import configured_progress
    return await _read(request, response, configured_progress)


@router.get("/api/memory-v2/library/memories")
async def memory_list(request: Request, response: Response, offset: int = Query(0, ge=0),
                      limit: int = Query(40, ge=1, le=100), query: str = Query("", max_length=500)):
    return await _read(request, response, library.list_memories, offset=offset, limit=limit, query=query)


@router.get("/memory/buckets")
async def compatibility_list(request: Request, response: Response, offset: int = Query(0, ge=0),
                             limit: int = Query(40, ge=1, le=100)):
    result = await _read(request, response, library.list_memories, offset=offset, limit=limit)
    return {**result, "buckets": result["memories"]}


@router.get("/api/memory-v2/library/memories/{memory_id}/sources")
async def memory_sources(memory_id: str, request: Request, response: Response,
                         offset: int = Query(0, ge=0), limit: int = Query(5, ge=1, le=5)):
    return await _read(request, response, library.memory_detail, memory_id, offset=offset, limit=limit, sources=True)


@router.get("/api/memory-v2/library/memories/{memory_id}")
async def memory_detail(memory_id: str, request: Request, response: Response,
                        offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    return await _read(request, response, library.memory_detail, memory_id, offset=offset, limit=limit)


@router.get("/api/memory-v2/library/archive")
async def archive_list(request: Request, response: Response, offset: int = Query(0, ge=0),
                       limit: int = Query(40, ge=1, le=100), query: str = Query("", max_length=500)):
    return await _read(request, response, library.archive_records, offset=offset, limit=limit, query=query)


@router.get("/api/memory-v2/library/archive/{memory_id}")
async def archive_detail(memory_id: str, request: Request, response: Response):
    return await _read(request, response, library.archive_detail, memory_id)


@router.get("/memory/bucket/{bucket_id}")
async def compatibility_detail(bucket_id: str, request: Request, response: Response):
    if bucket_id.startswith(("event:", "thread:", "group:")):
        return await _read(request, response, library.memory_detail, bucket_id)
    return await _read(request, response, library.archive_detail, bucket_id)


@router.get("/api/memory-v2/library/coverage")
async def coverage(request: Request, response: Response):
    return await _read(request, response, library.coverage_observation)
