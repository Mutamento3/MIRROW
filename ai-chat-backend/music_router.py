"""Legacy music API compatibility shell.

Playback, song resolution, playlists and account operations are owned by
``music_system.router`` under ``/api/music/v2``. The old upload/MCP/proxy
player endpoints stay registered only so stale clients receive an explicit
retirement response instead of silently activating a second music system.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/api/music", tags=["music"])
_RETIRED_PLAYER_DETAIL = "旧音乐上传/代理播放通道已退役，请使用音乐中枢和歌曲卡片"


def setup_music_router(*_args, **_kwargs):
    """Retain the former startup hook while old callers are phased out."""


def setup_music_management(*_args, **_kwargs):
    """Retain the former startup hook while old callers are phased out."""


class MusicModeRequest(BaseModel):
    mode: bool


@router.post("/upload")
async def upload_music_file():
    raise HTTPException(410, _RETIRED_PLAYER_DETAIL)


@router.get("/now-playing")
async def get_now_playing():
    raise HTTPException(410, _RETIRED_PLAYER_DETAIL)


@router.get("/search")
async def music_search():
    raise HTTPException(410, "旧搜索入口已退役，请使用 /api/music/v2/search")


@router.get("/stream/{song_id}")
async def music_stream(song_id: int):
    del song_id
    raise HTTPException(410, _RETIRED_PLAYER_DETAIL)


@router.get("/status")
async def get_music_status():
    """Compatibility projection of the v2 session for older status readers."""
    from music_system.service import get_service

    state = get_service().status()
    session = state.get("session")
    return {
        "music_mode": bool(session and session["status"] in {"playing", "paused"}),
        "now_playing": session,
    }


@router.post("/cochlea/now-playing")
async def post_now_playing(data: dict):
    del data
    raise HTTPException(410, "播放状态仅从原生 Relay 获取，请更新 MIRROW")


@router.get("/cochlea/status")
async def get_cochlea_status():
    from music_system.service import get_service

    return get_service().status()


@router.post("/mode")
async def set_music_mode(data: MusicModeRequest):
    del data
    raise HTTPException(410, "音乐模式已替换为共同播放会话，请使用音乐中枢")


@router.get("/qr")
async def get_music_login_qr():
    raise HTTPException(410, "请在新版音乐中枢连接网易云账号")


@router.post("/analyze")
async def trigger_music_analysis():
    raise HTTPException(410, "旧品味覆盖通道已退役，音乐经历由条件性每日整理处理")
