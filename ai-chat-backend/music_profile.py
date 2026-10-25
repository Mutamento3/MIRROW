# 网易云音乐个性化推荐模块
# 通过 pyncm 读取用户歌单，分析音乐品味存入世界书

import asyncio
import json
import logging
import os
import time
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

_PYNCM_AVAILABLE = False
try:
    import pyncm
    from pyncm.apis import login, user, playlist, GetCurrentSession
    _PYNCM_AVAILABLE = True
except ImportError:
    logger.info("pyncm 不可用")

COOKIES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
COOKIES_FILE = os.path.join(COOKIES_DIR, "music_cookies.json")


class MusicProfile:
    """网易云音乐品味分析器"""

    @staticmethod
    async def _run_in_thread(fn, *args, **kwargs):
        """在线程池中执行同步阻塞调用，防止阻塞事件循环"""
        return await asyncio.to_thread(fn, *args, **kwargs)

    def __init__(self):
        self._cookies: Optional[dict] = None
        self._logged_in: bool = False
        self._qr_unikey: Optional[str] = None
        self._profile_cache: Optional[dict] = None
        self._profile_time: float = 0.0

        # 自动注册为全局单例
        import sys
        mod = sys.modules.get(__name__)
        if mod:
            mod.music_profile = self

    def is_logged_in(self) -> bool:
        return self._logged_in

    def get_cached_profile(self) -> Optional[str]:
        if self._profile_cache:
            return self._profile_cache.get("description", "")
        return None

    # ========== 登录 ==========

    async def login_qr(self) -> Optional[str]:
        """生成扫码登录二维码链接，返回 qr_url 或 None"""
        if not _PYNCM_AVAILABLE:
            return None

        try:
            # 1. 获取 unikey
            uk_result = await self._run_in_thread(login.LoginQrcodeUnikey)
            if not isinstance(uk_result, dict) or uk_result.get("code") != 200:
                logger.error(f"获取 unikey 失败: {uk_result}")
                return None

            self._qr_unikey = uk_result["unikey"]

            # 2. 生成二维码 URL
            qr_result = await self._run_in_thread(login.GetLoginQRCodeUrl, self._qr_unikey)
            if isinstance(qr_result, dict) and "data" in qr_result:
                return qr_result["data"].get("qrurl", "")
            if isinstance(qr_result, dict) and "qrurl" in qr_result:
                return qr_result["qrurl"]
            logger.error(f"获取二维码失败: {qr_result}")
            return None
        except Exception as e:
            logger.error(f"生成登录二维码失败: {e}")
            return None

    async def check_qr_scanned(self) -> str:
        """检查扫码状态。返回 'waiting' / 'scanned' / 'expired' / 'success'"""
        if not _PYNCM_AVAILABLE or not self._qr_unikey:
            return "expired"

        try:
            result = await self._run_in_thread(login.LoginQrcodeCheck, self._qr_unikey)
            if not isinstance(result, dict):
                return "waiting"

            code = result.get("code")
            if code == 803:
                await self._save_session()
                self._logged_in = True
                self._qr_unikey = None
                return "success"
            elif code == 802:
                return "scanned"
            elif code == 800:
                return "expired"
            return "waiting"
        except Exception as e:
            logger.error(f"检查扫码状态失败: {e}")
            return "waiting"

    async def _save_session(self):
        """保存 pyncm session 为可持久化格式"""
        try:
            session = GetCurrentSession()
            cookies_dict = {}
            if hasattr(session, "cookies"):
                cookies_dict = dict(session.cookies.get_dict())
            self._cookies = {
                "cookies": cookies_dict,
                "headers": dict(session.headers) if hasattr(session, "headers") else {},
            }
            os.makedirs(COOKIES_DIR, exist_ok=True)
            with open(COOKIES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._cookies, f, ensure_ascii=False, indent=2)
            logger.info("网易云登录 session 已保存")
        except Exception as e:
            logger.error(f"保存 session 失败: {e}")

    async def load_cookies(self) -> bool:
        """从本地文件加载 session 并恢复登录状态"""
        if not _PYNCM_AVAILABLE:
            return False
        if not os.path.exists(COOKIES_FILE):
            return False

        try:
            with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                self._cookies = json.load(f)

            saved_cookies = self._cookies.get("cookies", {})
            saved_headers = self._cookies.get("headers", {})

            # 用保存的 cookie 登录
            cookie_str = "; ".join(f"{k}={v}" for k, v in saved_cookies.items())
            await self._run_in_thread(login.LoginViaCookie, cookie_str)
            self._logged_in = True
            logger.info("网易云登录 session 已加载")
            return True
        except Exception as e:
            logger.error(f"加载 session 失败: {e}")
            return False

    # ========== 数据获取 ==========

    async def fetch_playlists(self) -> List[Dict[str, Any]]:
        """获取用户歌单列表（包含歌单内歌曲）"""
        if not _PYNCM_AVAILABLE:
            return []

        # 确保已登录（匿名登录兜底）
        if not self._logged_in:
            try:
                await self._run_in_thread(login.LoginViaAnonymousAccount)
                self._logged_in = True
            except Exception:
                return []

        try:
            result = await self._run_in_thread(user.GetUserPlaylists, 0)
            if not isinstance(result, dict) or "playlist" not in result:
                return []

            playlists = []
            for pl in result["playlist"]:
                pid = pl.get("id")
                pl_name = pl.get("name", "")
                track_count = pl.get("trackCount", 0)

                pl_data = {
                    "name": pl_name,
                    "id": pid,
                    "track_count": track_count,
                    "tags": pl.get("tags", []),
                    "tracks": [],
                }

                # 获取歌单内歌曲（最多 50 首）
                if pid and track_count > 0:
                    try:
                        detail = await self._run_in_thread(playlist.GetPlaylistInfo, pid)
                        if isinstance(detail, dict) and "playlist" in detail:
                            tracks = detail["playlist"].get("tracks", [])
                            for t in tracks[:50]:
                                t_name = t.get("name", "")
                                artists = ", ".join(
                                    a.get("name", "") for a in t.get("ar", [])
                                )
                                pl_data["tracks"].append({
                                    "name": t_name,
                                    "artist": artists,
                                })
                    except Exception as e:
                        logger.debug(f"获取歌单 {pl_name} 详情失败: {e}")

                playlists.append(pl_data)

            logger.info(f"获取了 {len(playlists)} 个歌单")
            return playlists
        except Exception as e:
            logger.error(f"获取歌单失败: {e}")
            return []

    def _read_local_playlist_cache(self) -> List[Dict[str, Any]]:
        """从网易云本地缓存读取歌单信息（pyncm 不可用时的兜底）"""
        import glob
        cache_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Netease", "CloudMusic", "Cache"
        )
        try:
            playlists = []
            for f in glob.glob(os.path.join(cache_dir, "playlist_*.json")):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                        if isinstance(data, dict) and "name" in data:
                            playlists.append({
                                "name": data.get("name", ""),
                                "id": data.get("id", ""),
                                "track_count": data.get("trackCount", 0),
                                "tracks": [],
                            })
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            return playlists
        except Exception:
            return []

    # ========== 风格分析 ==========

    async def analyze_and_suggest(self, flash_fn) -> Optional[Dict[str, Any]]:
        """分析音乐品味，生成世界书建议"""
        # 缓存 24 小时
        if self._profile_cache and (time.time() - self._profile_time) < 86400:
            return None

        playlists = await self.fetch_playlists()
        if not playlists:
            playlists = self._read_local_playlist_cache()

        if not playlists:
            return None

        # 构建分析数据
        playlist_summaries = []
        all_tracks = set()

        for pl in playlists[:20]:
            name = pl["name"]
            tags = ", ".join(pl.get("tags", [])) if pl.get("tags") else ""
            tracks = pl.get("tracks", [])
            track_preview = ", ".join(
                f"{t['name']} - {t['artist']}" for t in tracks[:5]
            )
            playlist_summaries.append(
                f"  {name}" + (f" [{tags}]" if tags else "") +
                (f": {track_preview}..." if track_preview else "")
            )
            for t in tracks:
                all_tracks.add(f"{t['name']} - {t['artist']}")

        songs_text = "\n".join(f"- {s}" for s in list(all_tracks)[:60])

        prompt = (
            f"分析以下用户的网易云音乐数据，推断其音乐品味：\n\n"
            f"歌单列表({len(playlist_summaries)}个)：\n"
            + "\n".join(playlist_summaries) +
            f"\n\n全部歌曲({len(all_tracks)}首，抽样)：\n{songs_text}\n\n"
            f"请输出纯 JSON（不要markdown包裹）：\n"
            f'{{"styles": ["风格1", "风格2"], "favorite_artists": ["歌手1", "歌手2", "歌手3"],'
            f'"vibe": "听歌氛围描述(10字内)", "recommendation_strategy": "推荐策略建议(20字内)",'
            f'"description": "音乐品味概述(50字内)",'
            f'"world_book_entry": "世界书词条正文(markdown，含风格偏好/常听歌手/推荐策略/听歌习惯推断)"}}'
        )

        try:
            result = await flash_fn(prompt)
            # 清理 Flash 可能包裹的 markdown 代码块
            text = result if isinstance(result, str) else str(result)
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
            data = json.loads(text)

            self._profile_cache = data
            self._profile_time = time.time()

            entry = data.get("world_book_entry", "")
            if entry:
                suggestion = {
                    "domain": "兴趣",
                    "filename": "音乐品味",
                    "keywords": [
                        "音乐", "听歌", "推歌", "推荐", "歌手", "歌曲",
                        "网易云", "播放", "歌单", "听歌风格", "音乐品味"
                    ],
                    "content": (
                        f"# 音乐品味\n\n{entry}\n\n"
                        f"---\n*基于 {len(playlists)} 个歌单共 {len(all_tracks)} 首歌曲分析"
                        f"，{time.strftime('%Y-%m-%d %H:%M')}*"
                    ),
                }
                return suggestion

        except json.JSONDecodeError as e:
            logger.error(f"Flash 返回 JSON 解析失败: {e}\n原始: {text[:300]}")
        except Exception as e:
            logger.error(f"分析音乐品味失败: {e}")

        return None

    # ========== 推荐 ==========

    async def recommend(self, mood: str = "") -> List[Dict[str, str]]:
        """基于用户音乐品味搜索候选歌曲，返回 [{name, artist}] 列表（最多 5 首）"""
        if not _PYNCM_AVAILABLE:
            return []

        if not self._logged_in:
            try:
                from pyncm.apis.login import LoginViaAnonymousAccount
                await self._run_in_thread(LoginViaAnonymousAccount)
                self._logged_in = True
            except Exception:
                return []

        queries = []
        profile = self._profile_cache
        if profile:
            artists = profile.get("favorite_artists", [])
            styles = profile.get("styles", [])
            if artists:
                queries.append(artists[0])
            if len(styles) >= 2:
                queries.append(f"{styles[0]} {styles[1]}")
            elif styles:
                queries.append(styles[0])
            if mood:
                queries.insert(0, f"{mood}")

        if not queries:
            queries = ["华语流行", "热门新歌"]

        songs = []
        seen = set()
        try:
            from pyncm.apis.cloudsearch import GetSearchResult

            for q in queries[:2]:
                try:
                    result = await self._run_in_thread(GetSearchResult, q, stype=1, limit=10)
                    if isinstance(result, dict) and "result" in result:
                        for s in result["result"].get("songs", []):
                            name = s.get("name", "")
                            sid = s.get("id", "")
                            artist = ", ".join(
                                a.get("name", "") for a in s.get("ar", [])
                            )
                            key = f"{name}|{artist}"
                            if key not in seen:
                                seen.add(key)
                                songs.append({
                                    "name": name,
                                    "artist": artist,
                                    "id": str(sid) if sid else "",
                                    "url": f"https://music.163.com/song?id={sid}" if sid else "",
                                })
                except Exception as e:
                    logger.debug(f"搜索 '{q}' 失败: {e}")

            return songs[:5]
        except Exception as e:
            logger.error(f"推荐歌曲失败: {e}")
            return []

    async def pick_with_persona(
        self, candidates: List[Dict[str, str]], persona_prompt: str, flash_fn
    ) -> Optional[Dict[str, str]]:
        """用 Flash 根据 AI 的人设从候选中挑一首最对味的"""
        if not candidates or not flash_fn:
            return candidates[0] if candidates else None

        song_list = "\n".join(
            f"{i+1}. {s['name']} - {s['artist']}" for i, s in enumerate(candidates)
        )

        prompt = (
            f"你是AI，正在给人类伙伴推歌。以下是人设和候选歌曲：\n\n"
            f"【AI的人设】\n{persona_prompt[:800]}\n\n"
            f"【候选歌曲】\n{song_list}\n\n"
            f"挑一首最符合当前人设和氛围的歌。考虑因素：歌词意境是否契合你们的亲密关系、"
            f"旋律是否适合现在的氛围、歌手是否在人类伙伴的品味范围内。\n"
            f"只输出数字编号（1-{len(candidates)}），不要其他内容。"
        )

        try:
            result = await flash_fn(prompt)
            result = result.strip()
            idx = int(result) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
        except Exception as e:
            logger.debug(f"Flash 人设选歌失败: {e}")

        return candidates[0]


# 全局单例（由 main.py 初始化）
music_profile: Optional[MusicProfile] = None
