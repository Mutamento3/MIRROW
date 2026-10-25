"""Read-only profile compatibility until cognition retirement is completed."""
from typing import Optional


class ProfileManager:
    """Read-only transitional profile; current cognition remains authoritative."""
    async def get_profile(self, session_id: str = "") -> str:
        from cognition.retirement import completed
        if completed():
            return ""
        from memory_v2.legacy_archive import get_legacy_archive
        data = get_legacy_archive().profile_metadata().get("profile", {})
        if not isinstance(data, dict):
            return ""
        return "旧画像兼容材料:\n" + "\n".join(f"{key}: {value}" for key, value in data.items())

    async def close(self):
        pass


# 全局单例
_profile_manager: Optional[ProfileManager] = None


def get_global_profile_manager() -> ProfileManager:
    global _profile_manager
    if _profile_manager is None:
        _profile_manager = ProfileManager()
    return _profile_manager
