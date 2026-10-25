"""Complete lightweight song identities and factual candidate provenance."""
from __future__ import annotations

SUBJECT_LABELS = {"human": "人类伙伴", "k": "AI", "shared": "共有"}


def memberships(song: dict) -> list[dict[str, str]]:
    result = []
    seen = set()
    for item in song.get("playlist_memberships") or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        subject = str(item.get("subject") or "").strip()
        if not pid or subject not in SUBJECT_LABELS or pid in seen:
            continue
        seen.add(pid)
        result.append({"id": pid, "name": str(item.get("name") or "")[:100], "subject": subject})
    return result


def merge_shelf_candidates(cached: list[dict], shelf: list[dict]) -> list[dict]:
    """Keep cache material while attaching playlist ownership by exact song ID."""
    result = [dict(song) for song in cached]
    by_id = {str(song.get("netease_song_id")): song for song in result if song.get("netease_song_id")}
    for candidate in shelf:
        sid = str(candidate.get("netease_song_id") or "")
        if not sid:
            continue
        current = by_id.get(sid)
        if current is None:
            current = dict(candidate)
            result.append(current)
            by_id[sid] = current
        else:
            combined = memberships(current) + memberships(candidate)
            current["playlist_memberships"] = combined
        unique = {item["id"]: item for item in memberships(current)}
        current["playlist_memberships"] = list(unique.values())
        current["playlist_ids"] = list(unique)
        current["playlist_names"] = [item["name"] for item in unique.values()]
        current["music_shelf"] = bool(unique)
    return result


def recent_play_counts(recent_music: list[dict] | None) -> dict[str, int]:
    """Count recent real listening per exact fingerprint.

    This is a factual repetition tally, not a preference ranking: the
    caller's own window (e.g. the current active day's completed nodes) decides.
    """
    counts: dict[str, int] = {}
    for fact in recent_music or []:
        if not isinstance(fact, dict):
            continue
        fingerprint = str(fact.get("fingerprint") or "").strip()
        if fingerprint:
            counts[fingerprint] = counts.get(fingerprint, 0) + 1
    return counts


def candidate_catalog(cached: list[dict]) -> list[dict]:
    """All known identities in stable name order, independent of repetition."""
    # Exact IDs distinguish recordings/versions. Memberships are merged without
    # altering the input; cache material and playback history are not rankings.
    merged = merge_shelf_candidates([], cached)
    standalone = [dict(song) for song in cached if not song.get("netease_song_id")]
    by_fingerprint = {str(song.get("fingerprint") or ""): song for song in [*merged, *standalone]
                      if str(song.get("fingerprint") or "").strip()}
    result = [{"title": song.get("title") or "?", "artist": song.get("artist") or "?",
               "fingerprint": fingerprint, "netease_song_id": song.get("netease_song_id"),
               "playlist_memberships": memberships(song)}
              for fingerprint, song in by_fingerprint.items()]
    return sorted(result, key=lambda song: (str(song["title"]).casefold(),
                                           str(song["artist"]).casefold(), song["fingerprint"]))


def format_candidate_catalog(cached: list[dict], counts: dict[str, int] | None = None) -> str:
    catalog = candidate_catalog(cached)
    if not catalog:
        return ""
    if counts:
        lines = [f"本地已收录目录：{len(catalog)} 首，按名称排列"
                 f"（次数取自所给节点事实，不是偏好排名）"
                 f"；此处仅列身份与已绑定歌单归属。"]
    else:
        lines = [f"本地已收录目录：{len(catalog)} 首，按曲名／歌手／指纹排序；此处仅列身份与已绑定歌单归属。"]
    for i, song in enumerate(catalog, 1):
        parts = [f"{i}. {song['title']} - {song['artist']}", f"fingerprint: {song['fingerprint']}"]
        heard = (counts or {}).get(song["fingerprint"], 0)
        if heard:
            parts.append(f"近期听过 {heard} 次")
        shelves = [f"{SUBJECT_LABELS[item['subject']]}歌单《{item['name']}》(ID {item['id']})"
                   for item in song["playlist_memberships"]]
        if shelves:
            parts.append("已绑定歌单: " + "、".join(shelves))
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def selected_origin(selection: dict, song: dict) -> tuple[str, dict[str, str] | None]:
    """A song's membership alone cannot reveal which source AI chose."""
    if selection.get("from_cache") is not True:
        return "search_result", None
    source_id = str(selection.get("source_playlist_id") or "").strip()
    source = next((item for item in memberships(song) if item["id"] == source_id), None)
    if source:
        return source["subject"] + "_playlist_candidate", source
    return "cache_candidate", None


def annotate_selection(song: dict, selection: dict, shelf: list[dict]) -> dict:
    """Persist the chosen path separately from exact-ID playlist membership."""
    result = dict(song)
    sid = str(result.get("netease_song_id") or "")
    known = next((item for item in shelf if str(item.get("netease_song_id") or "") == sid), None)
    # Only the current, explicitly bound shelf can substantiate ownership.
    # Fetched/cache metadata may be stale or belong to an unbound playlist.
    known_memberships = memberships(known) if known else []
    result["playlist_memberships"] = known_memberships
    result["playlist_ids"] = [item["id"] for item in known_memberships]
    result["playlist_names"] = [item["name"] for item in known_memberships]
    origin, source = selected_origin(selection, result)
    result["selection_origin"] = origin
    result["source_playlist_id"] = source["id"] if source else ""
    result["source_playlist_name"] = source["name"] if source else ""
    result["source_playlist_subject"] = source["subject"] if source else ""
    return result
