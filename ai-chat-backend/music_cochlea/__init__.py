"""
音乐耳蜗 (Music Cochlea) — AI 的实时音乐感知系统

当音乐模式开启时，自动检测 PC/手机正在播放的歌曲，
解析歌词+元数据+记忆关联，让 AI 像共享耳机一样感知并评论音乐。
"""

from .models import SongIdentity, SongMetadata, CachedReaction, MusicCochleaState
from .cochlea import MusicCochlea, get_cochlea, setup_cochlea

__all__ = [
    "SongIdentity", "SongMetadata", "CachedReaction", "MusicCochleaState",
    "MusicCochlea", "get_cochlea", "setup_cochlea",
]
