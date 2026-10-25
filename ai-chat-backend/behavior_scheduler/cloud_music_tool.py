# 网易云音乐控制工具
# 按消息来源控制电脑或手机上的网易云 App；搜索只负责取得歌曲身份，不代理音频。

import re
import json
import base64
import time
import asyncio
import logging
import webbrowser
from typing import Optional
import asyncio

import httpx
import win32gui
import win32con
import win32process
import psutil

from .base_tool import BaseTool, ToolResult, ToolStatus
from .execution_context import get_request_platform

logger = logging.getLogger(__name__)

# ── 网易云歌曲身份查询（pyncm 优先、公开 API 回退）──
_NETEASE_SEARCH_AVAILABLE = False
try:
    import netease_api as _netease
    _NETEASE_SEARCH_AVAILABLE = _netease.is_available()
except ImportError:
    pass

PROCESS_NAME = "cloudmusic.exe"
MUSIC_ACTIVITY_ACTIONS = {"search_play", "resume", "pause", "next", "like"}
_last_recommended_query: str = ""

NETEASE_SEARCH_URL = "http://music.163.com/api/search/get/web"
NETEASE_LYRIC_URL = "http://music.163.com/api/song/lyric"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _get_music_mode() -> bool:
    try:
        import music_monitor as _mm
        return _mm.music_monitor.music_mode if _mm.music_monitor else False
    except Exception:
        return False


def _orpheus_command(cmd_obj: dict):
    """通过 orpheus:// Base64 协议发送命令到网易云"""
    json_str = json.dumps(cmd_obj, separators=(",", ":"))
    encoded = base64.b64encode(json_str.encode()).decode()
    webbrowser.open(f"orpheus://{encoded}")


class CloudMusicTool(BaseTool):
    name = "cloud_music"
    description = "按当前消息来源控制电脑或手机上的网易云音乐 App，支持搜索播放、推荐和播放控制。"
    flash_description = "网易云歌曲卡片与歌单——分享推荐、按来源设备单曲播放、读取已绑定歌单曲目、建歌单加减歌、安静陪听、读取歌词和保存感想"
    share_guide = "想用歌曲表达心情时，可分享歌曲卡片，附歌名和歌手；歌曲卡片不自动播放。"
    pro_guide = (share_guide + "也可引用真实歌曲标识。可分享歌单共听、点播、暂停或恢复共听，"
                 "以及调整单曲、列表、循环、跟随外部播放或结束共听。点播默认单曲播完停止；恢复只作用于已暂停会话。"
                 "播放控制按当前消息来源设备执行，状态以回执为准。可查已绑定歌单与曲目（每页至多30首），按查询结果推荐。"
                 "可建私密歌单、改名、加减歌曲，并指定漫想默认收藏处；AI 与共有歌单可编辑，删除限 AI 唱片架中当前账号创建的歌单。"
                 "操作引用真实歌单或歌曲标识，或能唯一定位的名称；保存歌曲、设置循环都需要真实调用。"
                 "还可读具体歌曲歌词、保存听歌感想或设置安静偏好。独立操作成功只呈现回执；需要根据查询结果继续行动时说明后续目的。")
    ui_only_actions = frozenset({"share", "recommend_by_style", "playlist_share", "playlist_create", "playlist_rename", "playlist_delete", "playlist_add", "playlist_remove", "playlist_play", "playlist_wander_default", "session_mode", "session_follow", "play", "resume", "pause", "next", "end", "quiet", "like", "search_play"})

    def get_result_delivery(self, **kwargs):
        # Pre-tool scheduling must know terminal actions before execution; failures
        # still need a model receipt once the real execution outcome is known.
        result = kwargs.get('_result')
        if kwargs.get('_execution_failed') or (result is not None and result.status != ToolStatus.SUCCESS):
            return 'model'
        if kwargs.get('continue_after'):
            return 'model'
        return super().get_result_delivery(**kwargs)

    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "search_play", "now_playing", "recommend_by_style", "music_login",
                    "resume", "pause", "next", "like",
                    "share", "playlist_share", "inspect", "note", "quiet", "end", "playlists", "playlist_tracks", "playlist_create", "playlist_rename", "playlist_delete", "playlist_add", "playlist_remove", "playlist_play", "playlist_wander_default", "session_mode", "session_follow",
                ],
            },
            "query": {"type": "string"},
            "song_id": {"type":"string"},
            "playlist_id": {"type":"string"},
            "offset": {"type":"integer", "minimum":0},
            "limit": {"type":"integer", "minimum":1, "maximum":30},
            "subject": {"type":"string","enum":["human","k","shared"]},
            "mode": {"type":"string","enum":["single","list","loop","one"]},
            "quiet": {"type":"boolean"},
            "follow_external": {"type":"boolean"},
            "selected": {"type":"boolean"},
            "reaction": {"type":"string"},
            "reason": {"type":"string", "maxLength":300, "description":"可选：AI 此次建歌单或改歌单的简短理由，作为 AI 的感想留存；没有就留空"},
            "continue_after": {"type":"boolean", "description":"本次结果还用于后续行动（如建歌单后继续加歌）时设 true；否则独立操作成功仅展示 UI 回执"},
        },
        "required": ["action"],
    }

    def get_user_facing_description(self, **kwargs) -> str:
        action = kwargs.get("action", "")
        query = kwargs.get("query", "")
        m = {
            "play": "正在播放", "resume": "正在恢复播放", "pause": "暂停播放",
            "next": "切到下一首", "playlist_tracks": "正在查看歌单曲目",
            "like": "已收藏",
            "search_play": f"正在搜索「{query}」并播放" if query else "正在搜索播放",
            "now_playing": "正在查看当前播放",
            "recommend_by_style": "正在为你推歌",
            "playlist_delete": "正在删除 AI 的网易云歌单",
            "session_mode": "正在调整 MIRROW 共听播放方式",
            "session_follow": "正在调整持续一起听",
            "music_login": "正在准备登录",
        }
        return m.get(action, f"正在使用网易云音乐: {action}")

    async def execute(self, action: str, query: str = "", **kwargs) -> ToolResult:
        if action in {"share", "recommend_by_style", "playlist_share"}:
            from music_system.sharing import enqueue
            return await enqueue(
                query, kwargs.get('song_id'), kwargs.get('_session_id', ''),
                kwargs.get('playlist_id') if action == 'playlist_share' else None,
                mode=kwargs.get('mode'), action='playlist_share' if action == 'playlist_share' else 'share',
                delivery_gate=kwargs.get('_delivery_gate'),
            )
        from music_system.tools import execute
        result = await execute(action, query, **kwargs)
        terminal = {"playlist_create", "playlist_rename", "playlist_delete", "playlist_add", "playlist_remove", "playlist_play", "playlist_wander_default", "session_mode", "session_follow", "play", "resume", "pause", "next", "end", "quiet", "like", "search_play"}
        if result.status == ToolStatus.SUCCESS and action in terminal and not kwargs.get('continue_after', False):
            result.delivery = 'ui_only'
        return result

    def set_push_reload(self, callback):
        from music_system.sharing import configure
        configure(callback)

    @staticmethod
    def _cancel_commanded_playback() -> None:
        try:
            from music_cochlea.commanded_playback import get_commanded_playback
            coordinator = get_commanded_playback()
            if coordinator:
                coordinator.cancel()
        except Exception:
            pass

    async def _ensure_music_mode(self) -> None:
        """A real music action silently starts the cochlea instead of being gated by it."""
        if _get_music_mode():
            return
        try:
            import music_monitor as _mm
            if _mm.music_monitor:
                _mm.music_monitor.set_music_mode(True)
            from shared_state import set_music_mode_active
            set_music_mode_active(True)
            from settings_manager import set_setting
            set_setting("music_mode_state", True)
            from music_cochlea.cochlea import get_cochlea
            cochlea = get_cochlea()
            if cochlea:
                await cochlea.set_active(True)
            logger.info("音乐工具调用已静默开启音乐模式")
        except Exception as exc:
            logger.warning("音乐模式自动开启不完整，继续执行真实控制: %s", exc)

    async def _mobile_relay(self, action: str, **params) -> ToolResult:
        try:
            from shared_state import get_mobile_relay_callback
            relay = get_mobile_relay_callback()
            if not relay:
                return ToolResult(status=ToolStatus.ERROR, content="", error="手机后台未连接")
            payload = {"action": action, **params}
            request_id = f"mnetease_{int(time.time() * 1000)}"
            response = await relay(request_id, "mobile_music_control", payload)
            if response and response.get("success"):
                content = response.get("content") or f"手机网易云已执行 {action}"
                return ToolResult(status=ToolStatus.SUCCESS, content=content,
                                  extra_data=response.get("data"))
            detail = (response or {}).get("error") or (response or {}).get("content") or "未知错误"
            return ToolResult(status=ToolStatus.ERROR, content="", error=f"手机网易云控制失败: {detail}")
        except Exception as exc:
            return ToolResult(status=ToolStatus.ERROR, content="", error=f"手机网易云控制失败: {exc}")

    async def _device_command(self, action: str) -> ToolResult:
        if get_request_platform() == "mobile":
            return await self._mobile_relay(action)
        return await self._simple_cmd(action)

    async def _play_song_on_device(self, song: dict, *, target: Optional[str] = None) -> ToolResult:
        sid = str(song.get("id", ""))
        name = song.get("name", "")
        artist = song.get("artist", "") or "未知歌手"
        duration_ms = int(song.get("duration") or 0)
        requested_device = str(target or get_request_platform() or "computer").strip().lower()
        # ``pc`` is the historical request-platform spelling; keep it in the
        # public receipt for compatibility while routing it through the
        # explicit computer implementation.
        device = {"pc": "computer", "desktop": "computer"}.get(requested_device, requested_device)
        if device not in {"computer", "mobile"}:
            return ToolResult(status=ToolStatus.ERROR, content="", error=f"不支持的音乐外放目标: {device}")
        if device == "mobile":
            result = await self._mobile_relay(
                "play_song", song_id=sid, title=name, artist=artist,
                duration_ms=duration_ms,
            )
        else:
            try:
                _orpheus_command({"type": "song", "id": sid, "cmd": "play"})
                confirmed = False
                for _ in range(16):
                    await asyncio.sleep(0.25)
                    title = self._get_song_title().lower()
                    if name.lower() in title and (not artist or artist.lower() in title):
                        confirmed = True
                        break
                if confirmed:
                    result = ToolResult(status=ToolStatus.SUCCESS,
                                        content=f"正在电脑网易云播放《{name}》- {artist}")
                else:
                    result = ToolResult(
                        status=ToolStatus.ERROR, content="",
                        error="点播指令已交给电脑网易云，但 4 秒内未从窗口状态确认歌曲开始播放",
                    )
            except Exception as exc:
                result = ToolResult(status=ToolStatus.ERROR, content="", error=str(exc))
        if result.status == ToolStatus.SUCCESS:
            result.extra_data = {
                **(result.extra_data or {}),
                "device": requested_device if target is None and requested_device == "pc" else device,
                "song": {"id": sid, "name": name, "artist": artist,
                         "duration": duration_ms},
            }
            try:
                from music_cochlea.commanded_playback import get_commanded_playback
                coordinator = get_commanded_playback()
                if coordinator:
                    coordinator.register(
                        device, result.extra_data["song"]
                    )
            except Exception as exc:
                logger.warning("AI 单曲托管注册失败: %s", exc)
        return result

    async def play_song_by_id(
        self,
        song_id: str,
        *,
        title: str = "",
        artist: str = "",
        duration_ms: int = 0,
        target: str = "computer",
    ) -> ToolResult:
        """Play one already-identified song on an explicit device.

        Wander uses this narrow primitive for mobile playback so it shares the
        normal Mobile Relay/MediaSession receipt path.  ``target=mobile`` is
        intentionally terminal on relay failure; it never falls back to the
        computer player behind the caller's back.
        """
        from music_system.service import get_service
        from music_system.errors import MusicSystemError
        service = get_service()
        if service.status()["session"]:
            return ToolResult(status=ToolStatus.ERROR, content="", error="已有共同播放会话，漫想未抢占外放")
        try:
            song = await service.provider.song(str(song_id))
            state = await service.start_song(song, "computer" if target in {"pc", "desktop"} else target, "single", subject="k")
            return ToolResult(status=ToolStatus.SUCCESS, content="网易云设备回执：" + state["status"],
                              extra_data={"song": song, "music_card": song, "device": state["device"],
                                          "music_session_id": state["id"],
                                          "pause_timeout_seconds": state["pause_timeout_seconds"]})
        except MusicSystemError as error:
            return ToolResult(status=ToolStatus.ERROR,content="",error=str(error))

    @staticmethod
    def _select_best_song(query: str, songs: list[dict]) -> dict | None:
        """Prefer candidates whose explicit title/artist facts occur in the query."""
        if not songs:
            return None

        def normalize(value: str) -> str:
            return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())

        normalized_query = normalize(query)

        def score(song: dict) -> tuple[int, int]:
            name = normalize(song.get("name", ""))
            artist = normalize(song.get("artist", ""))
            value = 0
            if name and name == normalized_query:
                value += 120
            elif name and name in normalized_query:
                value += 70
            if artist and artist in normalized_query:
                value += 60
            if name and artist and name + artist in normalized_query:
                value += 30
            # Stable tie-break: preserve API order instead of inventing a preference.
            return value, -songs.index(song)

        return max(songs, key=score)

    # ========== 简单命令 ==========

    async def _simple_cmd(self, action: str) -> ToolResult:
        try:
            _orpheus_command({"cmd": action})
            await asyncio.sleep(0.2)
            return ToolResult(status=ToolStatus.SUCCESS, content=f"已执行: {action}")
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content="", error=str(e))

    # ========== 窗口信息 ==========

    def _find_orpheus(self) -> Optional[int]:
        """查找网易云音乐窗口（与 music_monitor._find_cloudmusic_window 对齐，无类名过滤）"""
        result = []
        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if psutil.Process(pid).name().lower() == PROCESS_NAME:
                    title = win32gui.GetWindowText(hwnd)
                    if title and len(title) > 1:
                        result.append(hwnd)
                        return False
            except Exception:
                pass
            return True
        try:
            win32gui.EnumWindows(cb, None)
        except Exception:
            pass
        return result[0] if result else None

    def _get_song_title(self) -> str:
        try:
            hwnd = self._find_orpheus()
            if hwnd:
                return win32gui.GetWindowText(hwnd)
        except Exception:
            pass
        return ""

    # ========== 搜索播放 ==========

    async def _search_and_play(self, query: str) -> ToolResult:
        global _last_recommended_query
        if not query:
            return ToolResult(status=ToolStatus.ERROR, content="", error="请提供歌名")

        _last_recommended_query = query

        # 搜索只取得网易云歌曲身份，播放始终交给消息来源设备上的网易云 App。
        if _NETEASE_SEARCH_AVAILABLE:
            try:
                songs = await asyncio.to_thread(_netease.search_songs, query, 10)
                if songs:
                    selected = self._select_best_song(query, songs)
                    remaining = [song for song in songs if song is not selected]
                    result = await self._play_song_on_device(selected)
                    if result.status == ToolStatus.SUCCESS:
                        result.extra_data = {
                            **(result.extra_data or {}),
                            "candidates": remaining,
                            "source": "netease_app",
                        }
                        try:
                            from music_cochlea.commanded_playback import get_commanded_playback
                            coordinator = get_commanded_playback()
                            if coordinator:
                                coordinator.update_candidates(remaining)
                        except Exception:
                            pass
                    return result
            except Exception as exc:
                logger.debug("pyncm 搜索失败，回退公开搜索: %s", exc)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    NETEASE_SEARCH_URL,
                    params={"s": query, "type": 1, "limit": 10},
                    headers=HEADERS
                )
            data = resp.json()

            if data.get("code") == 200 and data["result"].get("songs"):
                candidates = []
                for raw_song in data["result"]["songs"]:
                    artists = raw_song.get("artists", raw_song.get("ar", []))
                    candidates.append({
                        "id": raw_song.get("id"),
                        "name": raw_song.get("name", ""),
                        "artist": " / ".join(a.get("name", "") for a in artists if a.get("name")),
                        "duration": raw_song.get("duration", raw_song.get("dt", 0)),
                    })
                selected = self._select_best_song(query, candidates)
                return await self._play_song_on_device(selected)

            return ToolResult(status=ToolStatus.SUCCESS, content=f"没搜到「{query}」，换个关键词试试？")
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content="", error=str(e))

    # ========== 每日推荐 ==========

    async def _daily_recommend(self) -> ToolResult:
        if get_request_platform() == "mobile":
            return await self._mobile_relay("daily_recommend")
        try:
            _orpheus_command({"type": "playlist", "cmd": "daily"})
            await asyncio.sleep(0.5)
            return ToolResult(status=ToolStatus.SUCCESS, content="已打开每日推荐")
        except Exception as e:
            return ToolResult(status=ToolStatus.ERROR, content="", error=str(e))

    # ========== 当前播放 ==========

    def _now_playing(self) -> ToolResult:
        return self._now_playing_sync()

    async def _now_playing_async(self) -> ToolResult:
        """读取当前消息来源设备上的网易云播放状态。"""
        if get_request_platform() == "mobile":
            return await self._mobile_relay("now_playing")
        return self._now_playing_sync()

    def _now_playing_sync(self) -> ToolResult:
        """读取电脑网易云窗口标题。"""
        title = self._get_song_title()
        if not title:
            return ToolResult(status=ToolStatus.SUCCESS, content="当前未在播放歌曲")
        if " - " in title:
            song, artist = title.split(" - ", 1)
            return ToolResult(status=ToolStatus.SUCCESS, content=f"当前播放：《{song}》- {artist}")
        return ToolResult(status=ToolStatus.SUCCESS, content=f"窗口标题: {title}")

    # ========== 个性化推荐 ==========

    async def _recommend_by_style(self) -> ToolResult:
        try:
            import music_profile as _mp
            mp = _mp.music_profile
        except Exception:
            return ToolResult(status=ToolStatus.ERROR, content="", error="音乐推荐模块未就绪")
        if not mp:
            return ToolResult(status=ToolStatus.ERROR, content="", error="音乐推荐模块未就绪")

        songs = await mp.recommend()
        print(f"[CLOUD_MUSIC] recommend() returned {len(songs)} songs: {[(s.get('name','?')) for s in songs[:3]]}", flush=True)
        if not songs:
            return ToolResult(
                status=ToolStatus.SUCCESS,
                content="我还不了解你的音乐品味呢~告诉我你喜欢什么风格的歌，或者先登录网易云~"
            )

        best = songs[0]
        profile = mp._profile_cache
        if profile:
            fav = {a.lower() for a in profile.get("favorite_artists", [])}
            if fav:
                for s in songs:
                    if s["artist"].lower() in fav:
                        best = s
                        break

        sid = best.get("id", "")
        name = best.get("name", "")
        artist = best.get("artist", "")

        global _last_recommended_query
        _last_recommended_query = f"{name} {artist}"

        # 推荐算法负责选歌，播放仍交给当前消息来源设备的网易云 App。
        if sid:
            result = await self._play_song_on_device(best)
            if result.status == ToolStatus.SUCCESS:
                result.extra_data = {
                    **(result.extra_data or {}),
                    "candidates": songs[1:4],
                    "source": "netease_app_recommendation",
                }
            return result

        # ③ 没有 ID 时用 Web API 搜索
        return await self._search_and_play(f"{name} {artist}")

    # ========== 音乐模式 ==========

    async def _toggle_music_mode(self) -> ToolResult:
        try:
            import music_monitor as _mm
            if _mm.music_monitor:
                new_mode = not _mm.music_monitor.music_mode
                _mm.music_monitor.set_music_mode(new_mode)
                # 同步 shared_state + MusicCochlea（对齐 /api/music/mode 端点的三步同步）
                from shared_state import set_music_mode_active
                set_music_mode_active(new_mode)
                from settings_manager import set_setting
                set_setting("music_mode_state", new_mode)
                from music_cochlea.cochlea import get_cochlea
                cochlea = get_cochlea()
                if cochlea:
                    await cochlea.set_active(new_mode)
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    content="已开启音乐模式！" if new_mode else "已关闭音乐模式。"
                )
        except Exception:
            pass
        return ToolResult(status=ToolStatus.ERROR, content="", error="音乐模式模块未就绪")

    async def _music_login(self) -> ToolResult:
        try:
            import music_profile as _mp
            mp = _mp.music_profile
            if mp:
                qr_url = await mp.login_qr()
                if qr_url:
                    print(f"[网易云登录] 二维码链接: {qr_url}", flush=True)
                    return ToolResult(status=ToolStatus.NEED_USER_INPUT, content="请用网易云App扫码登录，链接已打印到控制台。")
        except Exception:
            pass
        return ToolResult(status=ToolStatus.ERROR, content="", error="登录模块未就绪")

    async def _fetch_lyrics(self, song_id: int) -> str:
        """从网易云 API 拉取歌词，返回纯文本（时间轴已剥离）。"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    NETEASE_LYRIC_URL,
                    params={"id": song_id, "lv": 1},
                    headers=HEADERS,
                )
            data = resp.json()
            if data.get("code") == 200:
                lrc = data.get("lrc", {}).get("lyric", "")
                tlyric = data.get("tlyric", {}).get("lyric", "")  # 翻译歌词
                # 剥离时间轴 [mm:ss.xx]，保留纯文本
                import re
                lines = []
                for raw in [lrc, tlyric]:
                    for line in raw.split("\n"):
                        cleaned = re.sub(r"\[.*?\]", "", line).strip()
                        if cleaned and cleaned not in lines:
                            lines.append(cleaned)
                return "\n".join(lines)
        except Exception as e:
            logger.warning(f"歌词获取失败: {e}")
        return ""
