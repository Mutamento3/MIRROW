"""A read model over existing music, game and Xiaohongshu records.

No experience database is created here.  Each hit retains its original ID,
speaker, evidence type and limits; a search hit never becomes a preference.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
_BRACKETED = re.compile(r"[《「『](.{2,100}?)[》」』]")


@dataclass(frozen=True)
class Experience:
    id: str
    domain: str
    title: str
    artist: str
    happened_at: str
    fact: str
    feeling: str = ""
    subject: str = "k"
    object_id: str = ""
    aliases: tuple[str, ...] = ()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in text if char.isalnum())


def _clip(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _read_db(path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(sql, params).fetchall()
    except (sqlite3.Error, OSError):
        return []


def _music_notes() -> list[Experience]:
    from cognition.music_phases import experience_phases
    try:
        from cognition.music import records
        items = records()
    except Exception:
        return []
    result = []
    modes = {"listening": "AI 的听歌体验",
             "analysis": "仅作歌曲材料分析，未由本记录证明外放",
             "observed_playback": "有播放观察记录",
             "manual_note": "手动音乐记录"}
    for item in items[-1200:]:
        if item.get("subject_id") not in {"k", "human", "shared"}:
            continue
        title = _clip(item.get("title"), 160)
        if not title:
            continue
        phases = experience_phases(item)
        notes = phases["dimensions"]
        feeling = _clip(phases["reaction"], 340)
        if not feeling:
            feeling = _clip("；".join(str(value) for value in notes.values()), 340)
        fact = modes.get(str(item.get("mode")), "音乐记录")
        if phases["selection_reason"]:
            fact += "；当次选歌前的起意：" + _clip(phases["selection_reason"], 340)
        result.append(Experience(
            id="music-note:" + str(item.get("id") or ""), domain="music",
            title=title, artist=_clip(item.get("artist"), 100),
            happened_at=str(item.get("created_at") or ""),
            fact=fact, feeling=feeling,
            subject=str(item.get("subject_id")), object_id=str(item.get("source_id") or ""),
        ))
    return result


def _music_library() -> list[Experience]:
    path = ROOT / "data" / "music_system.sqlite"
    rows = _read_db(path, "SELECT id,occurred_at,event_type,playlist_id,subject,details_json "
                          "FROM music_library_events ORDER BY id DESC LIMIT 1200")
    verbs = {"created": "创建了网易云歌单", "renamed": "改名了网易云歌单",
             "deleted": "删除了网易云歌单", "tracks_added": "将歌曲加入网易云歌单",
             "tracks_removed": "从网易云歌单移除歌曲"}
    result = []
    for row in rows:
        kind = str(row["event_type"])
        if kind not in verbs:
            continue
        try:
            details = json.loads(row["details_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if kind.startswith("tracks_") and not details.get("verified_change"):
            # Historical rows could have been emitted by a no-op add/remove.
            continue
        playlist = _clip(details.get("name") or details.get("after"), 100)
        song = _clip(details.get("song_title"), 160)
        song_ids = [str(x) for x in (details.get("song_ids") or [])[:5]]
        target = f"《{song}》" if song else ("歌曲 ID " + "、".join(song_ids) if song_ids else "")
        fact = f"{verbs[kind]}《{playlist}》" + (f"：{target}" if target else "") + "；网易云变更已确认"
        aliases = tuple(x for x in (playlist, song, *song_ids) if x)
        result.append(Experience(
            id=f"music-library:{row['id']}", domain="music",
            title=song or playlist, artist=_clip(details.get("song_artist"), 100),
            happened_at=str(row["occurred_at"]), fact=fact,
            feeling=_clip(details.get("reason"), 300),
            subject="k" if details.get("source") in {"wander", "k_tool"} else str(row["subject"] or "unknown"),
            object_id=str(row["playlist_id"]), aliases=aliases,
        ))
    return result


def _wander_nodes() -> list[Experience]:
    path = ROOT / "events" / "wander_runtime.db"
    xhs_actions = {str(row["node_id"]): row for row in _read_db(
        path, "SELECT r.node_id,r.post_key,r.canonical_post_key,r.saved,a.reason FROM xhs_post_refs r "
              "LEFT JOIN xhs_post_actions a ON a.action_id=(SELECT a2.action_id "
              "FROM xhs_post_actions a2 WHERE a2.post_key=r.post_key AND a2.action='save' "
              "ORDER BY a2.created_at DESC,a2.rowid DESC LIMIT 1)"
    )}
    xhs_reports = {str(row["node_id"]): int(row["n"]) for row in _read_db(
        path, "SELECT node_id,COUNT(*) AS n FROM xhs_post_comment_reports GROUP BY node_id"
    )}
    rows = _read_db(path, "SELECT n.node_id,n.source_payload,n.source_summary,n.reflection,n.side_effect_refs,"
                          "n.completed_at,a.summary AS activity_summary,a.activity_type FROM wander_nodes n "
                          "JOIN wander_activities a ON a.activity_id=n.activity_id "
                          "WHERE a.activity_type IN ('listen_music','browse_xiaohongshu','play_game') "
                          "AND n.execution_status='succeeded' ORDER BY n.created_at DESC LIMIT 1200")
    result = []
    for row in rows:
        try:
            payload = json.loads(row["source_payload"] or "{}")
            refs = json.loads(row["side_effect_refs"] or "{}")
        except (TypeError, ValueError):
            continue
        if row["activity_type"] == "listen_music":
            receipt = refs.get("music_library_action") or {}
            if receipt.get("status") != "completed":
                continue
            playlist = _clip(receipt.get("playlist_name"), 100)
            title = _clip(payload.get("title"), 160)
            artist = _clip(payload.get("artist"), 100)
            kind = str(receipt.get("action") or "")
            action = {"create_playlist": "创建", "rename_playlist": "改名",
                      "delete_playlist": "删除", "add_current_song": "加入歌曲到",
                      "remove_song": "从中移除歌曲", "set_default_playlist": "设为默认收藏处"}.get(kind, "操作")
            if kind in {"add_current_song", "remove_song"} and receipt.get("changed") is False:
                action_fact = ("AI 漫想时核验歌曲原本就在" if kind == "add_current_song"
                               else "AI 漫想时核验歌曲原本不在") + f"网易云歌单《{playlist}》"
            elif kind in {"add_current_song", "remove_song"} and receipt.get("changed") is not True:
                action_fact = f"AI 漫想时请求{action}网易云歌单《{playlist}》，旧回执未核验是否实际变更"
            else:
                action_fact = f"AI 漫想时已确认{action}网易云歌单《{playlist}》"
            result.append(Experience(
                id="wander-music-action:" + str(row["node_id"]), domain="music",
                title=title or playlist, artist=artist, happened_at=str(row["completed_at"] or ""),
                fact=f"{action_fact}；本节点歌曲《{title}》为材料来源，未由此证明实际外放",
                feeling=_clip(receipt.get("reason") or row["reflection"], 300),
                subject="k", object_id=str(receipt.get("playlist_id") or ""),
                aliases=tuple(x for x in (playlist, title, str(receipt.get("song_id") or "")) if x),
            ))
            continue
        if row["activity_type"] == "play_game":
            if payload.get("game") not in {"detroit", "moonlit"}:
                continue
            title = _clip(payload.get("game_name"), 100)
            result.append(Experience(
                id="wander-game:" + str(row["node_id"]), domain="game",
                title=title, artist="", happened_at=str(row["completed_at"] or ""),
                fact="虚构游戏内 AI 实际执行的一步：" + _clip(row["source_summary"], 420)
                     + ("；该周目已结束：" + _clip(row["activity_summary"], 300)
                        if payload.get("game_complete") and row["activity_summary"] else ""),
                feeling=_clip(row["reflection"], 300), subject="k",
                object_id=str(row["node_id"]),
                aliases=tuple(x for x in (str(payload.get("game") or ""),
                                          str(payload.get("playthrough_key") or "")) if x),
            ))
            continue
        if payload.get("status") != "success" or not payload.get("source_id"):
            continue
        summary = _clip(payload.get("content_summary"), 360)
        if not summary:
            continue
        title = _clip(payload.get("title"), 100)
        if len(title) > 80:
            title = summary[:80]
        keep = xhs_actions.get(str(row["node_id"]))
        saved = bool(keep and keep["saved"])
        new_material = payload.get('material_version') == 1
        received = not new_material or refs.get('xhs_material_received') is True
        reflection = (row['reflection'] if refs.get('xhs_review_succeeded') is True else '') if new_material else (
            row['reflection'] or payload.get('reflection'))
        result.append(Experience(
            id="xhs-node:" + str(row["node_id"]), domain="xiaohongshu",
            title=title or summary[:80], artist="", happened_at=str(row["completed_at"] or ""),
            fact=(f"AI 已读取小红书当前可见帖子材料：{summary}；" if received else
                  f"专用手机已取得小红书可见帖子材料：{summary}；该节点尚无AI复核接收回执；") +
                 ("AI 随后将该帖留在 MIRROW 本地；" if saved else "") +
                 ("人类伙伴后来在 MIRROW 自述已评论，未核验平台评论；" if xhs_reports.get(str(row["node_id"])) else "") +
                 "未由此证明平台点赞、收藏或发布",
            feeling=_clip((keep["reason"] if saved and keep and keep["reason"] else
                           reflection), 300),
            subject="k", object_id=str(row["node_id"]),
            aliases=tuple(x for x in ((str(keep["post_key"]), str(keep["canonical_post_key"] or "")) if keep else ()) if x),
        ))
    return result


def _xhs_message_cards() -> list[Experience]:
    """Read actually sent 人类伙伴 cards from the message authority, not drafts."""
    path = ROOT / "events" / "event_chronicle.db"
    rows = _read_db(path, "SELECT message_id,long_text_card,timestamp FROM conversation_messages "
                          "WHERE role='user' AND long_text_card IS NOT NULL "
                          "ORDER BY timestamp DESC LIMIT 1200")
    result = []
    for row in rows:
        try:
            card = json.loads(row["long_text_card"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(card, dict) or card.get("kind") != "xhs":
            continue
        try:
            from xhs_posts.links import parse_share_link
            link = parse_share_link(str(card.get("link") or ""))
        except ValueError:
            continue
        title = _clip(card.get("title"), 100) or "人类伙伴分享的一篇帖子"
        note = _clip(card.get("body"), 250)
        result.append(Experience(
            id="xhs-message:" + str(row["message_id"]), domain="xiaohongshu",
            title=title, artist="", happened_at=str(row["timestamp"] or ""),
            fact="人类伙伴在消息卡片中分享了小红书链接；未由此证明 AI 已读帖子" +
                 (f"；人类伙伴的附言：{note}" if note else ""),
            subject="human", object_id=link["post_key"],
            aliases=(link["url"].split("?", 1)[0],),
        ))
    return result


def _game_events() -> list[Experience]:
    path = ROOT / "events" / "rift_archive.db"
    rows = _read_db(path, "SELECT e.id,e.game_id,e.actor,e.summary,e.observed_at,"
                          "g.title,g.case_id FROM rift_events e "
                          "JOIN rift_games g ON g.game_id=e.game_id "
                          "ORDER BY e.id DESC LIMIT 800")
    return [Experience(
        id=f"rift-event:{row['id']}", domain="game",
        title=_clip(row["title"] or row["case_id"], 120), artist="",
        happened_at=str(row["observed_at"] or ""),
        fact="虚构游戏内发生的记录：" + _clip(row["summary"], 420),
        subject="k" if row["actor"] == "k" else str(row["actor"] or "unknown"),
        object_id=str(row["game_id"]),
    ) for row in rows if row["summary"]]


def _xhs_publications() -> list[Experience]:
    # Both entrances resolve to this one publication identity, not two copies.
    from xhs_publish.store import receipt
    from xhs_publish.service import experience
    rows = _read_db(ROOT/'events'/'xhs_publish.db',
                    "SELECT record FROM publications ORDER BY rowid DESC LIMIT 1200")
    result=[]
    for row in rows:
        try:
            record=json.loads(row['record']);value=receipt(record)
            if value['status'] not in {'published','previewed','submitting','submitted_unverified','preparing'}:
                continue
            result.append(Experience(id='xhs-publication:'+value['task_id'],domain='xiaohongshu',
                title=value['title'],artist='',happened_at=record['updated_at'],fact=experience(value),
                subject='k',object_id=value['task_id']))
        except (ValueError,KeyError,TypeError):
            continue
    return result


def _xhs_interaction_replies() -> list[Experience]:
    from xhs_interactions.context import brief, capture
    from xhs_interactions.archive import _payload
    from xhs_interactions.store import reply_receipt
    batches = _read_db(ROOT/'events'/'xhs_interactions.db',
                       "SELECT record FROM attention ORDER BY rowid DESC LIMIT 1200")
    result, covered = [], set()
    for row in batches:
        try:
            record=json.loads(row['record'])
            if record.get('state') != 'completed': continue
            payload=_payload(record); view=capture(payload)
            covered.update(view['refs']['reply_ids'])
            targets=[item.get('target') or {} for item in payload.get('materials') or []]
            titles=list(dict.fromkeys(item.get('note_title') or '' for item in targets))
            result.append(Experience(id='xhs-attention:'+record['batch_id'], domain='xiaohongshu',
                title='、'.join(filter(None,titles)) or '小红书评论与艾特', artist='',
                happened_at=record.get('created_at') or '', fact=brief(view,include_feeling=False),
                feeling=view['reflection'], subject='k', object_id=record['batch_id'],
                aliases=tuple(dict.fromkeys(item.get('actor_name') or '' for item in targets))))
        except (ValueError,KeyError,TypeError): continue
    rows = _read_db(ROOT/'events'/'xhs_interactions.db',
                    "SELECT record FROM replies ORDER BY rowid DESC LIMIT 1200")
    for row in rows:
        try:
            value = json.loads(row['record'])
            if value['state'] not in {'published', 'previewed', 'submitting', 'submitted_unverified'}:
                continue
            if value['id'] in covered: continue
            result.append(Experience(id='xhs-interaction-reply:' + value['id'], domain='xiaohongshu',
                title=str(value['target'].get('note_title') or ''), artist='', happened_at=value['updated_at'],
                fact=brief({'xhs_reply': reply_receipt(value)}), subject='k', object_id=value['id']))
        except (ValueError, KeyError, TypeError):
            continue
    return result


def records(domains: Iterable[str] | None = None, *, include_wander: bool = True) -> list[Experience]:
    selected = set(domains or ("music", "game", "xiaohongshu"))
    result: list[Experience] = []
    if "music" in selected:
        result.extend(_music_notes())
        result.extend(_music_library())
    if "game" in selected:
        result.extend(_game_events())
    if "xiaohongshu" in selected:
        result.extend(_xhs_message_cards())
        result.extend(_xhs_publications())
        result.extend(_xhs_interaction_replies())
    if include_wander and selected & {"music", "xiaohongshu", "game"}:
        result.extend(item for item in _wander_nodes() if item.domain in selected)
    return result


def _auto_score(query: str, item: Experience) -> int:
    q = _norm(query)
    quoted = {_norm(text) for text in _BRACKETED.findall(query)}
    if item.object_id and item.object_id.isdigit() and item.object_id in query:
        return 125
    names = [item.title, *item.aliases]
    name_hits = []
    for name in names:
        key = _norm(name)
        if not key or key.isdigit():
            continue
        if key in quoted:
            name_hits.append(110)
        elif len(key) >= 3 and key in q:
            name_hits.append(95)
        elif quoted and len(key) >= 3 and any(SequenceMatcher(None, key, value).ratio() >= .86
                                                for value in quoted):
            name_hits.append(80)
    if name_hits:
        score = max(name_hits)
        if any(word in query for word in ("建", "创建", "怎么来的")) and "创建" in item.fact:
            score += 25
        if any(word in query for word in ("加进", "加入", "收进", "收藏")) and "加入" in item.fact:
            score += 15
        return score
    artist = _norm(item.artist)
    if item.domain == "music" and len(artist) >= 3 and artist in q:
        return 65
    return 0


def search_records(query: str, *, domains: Iterable[str] | None = None,
                   limit: int = 3, automatic: bool = False) -> list[Experience]:
    if not str(query or "").strip():
        return []
    selected = set(domains or ("music", "game", "xiaohongshu"))
    candidates = records(selected, include_wander=not automatic)
    if automatic:
        initial = any(_auto_score(query, item) for item in candidates if item.domain == "music")
        wander_domains = ({"music"} if initial and "music" in selected else set())
        if "xiaohongshu" in selected and (any(token in query for token in ("小红书", "帖子", "笔记"))
                                          or _BRACKETED.search(query)):
            wander_domains.add("xiaohongshu")
        if "game" in selected and any(name in query for name in ("底特律", "月幕万象", "detroit", "moonlit")):
            wander_domains.add("game")
        if wander_domains:
            candidates.extend(item for item in _wander_nodes() if item.domain in wander_domains)
    scored = [(score, item) for item in candidates if (score := _auto_score(query, item))]
    scored.sort(key=lambda pair: (pair[0], pair[1].happened_at), reverse=True)
    if automatic and scored and scored[0][0] < 65:
        return []
    seen: set[str] = set()
    seen_nodes: set[str] = set()
    result = []
    for _, item in scored:
        if item.id in seen:
            continue
        node_id = (item.id.removeprefix("wander-music-action:")
                   if item.id.startswith("wander-music-action:") else
                   item.object_id.removeprefix("wander-node:")
                   if item.object_id.startswith("wander-node:") else "")
        if node_id and node_id in seen_nodes:
            continue
        if node_id:
            seen_nodes.add(node_id)
        seen.add(item.id)
        result.append(item)
        if len(result) >= max(1, min(limit, 10)):
            break
    return result


def _render(item: Experience) -> str:
    from behavior_scheduler.bookshelf_search import sanitize_archive_text
    labels = {"music": "音乐", "game": "虚构游戏", "xiaohongshu": "小红书"}
    subject = {"k": "AI", "human": "人类伙伴", "shared": "共同"}.get(item.subject, "主体未确认")
    parts = [f"[{labels.get(item.domain, item.domain)} · {item.happened_at[:16]} · {subject} · 来源 {item.id}]",
             f"对象：《{item.title}》" + (f"—{item.artist}" if item.artist else ""), item.fact]
    if item.feeling:
        parts.append(f"{subject}当时的感想：{item.feeling}（自述，不是外部行动证据）")
    return sanitize_archive_text("\n".join(parts), limit=650)


def auto_context(query: str) -> str:
    hits = search_records(query, automatic=True, limit=2)
    if not hits:
        return ""
    return "【与当前话题相关的经历记录】\n" + "\n".join(_render(item) for item in hits)


def bookshelf_documents(domain: str = "", *, items: Iterable[Experience] | None = None) -> list[Any]:
    from behavior_scheduler.bookshelf_search import SearchDocument
    selected = {domain} if domain in {"music", "game", "xiaohongshu"} else None
    return [SearchDocument(
        id=item.id, source="experiences", text=_render(item),
        date=item.happened_at[:10], session_id=item.id,
        metadata={"domain": item.domain, "source_id": item.object_id},
    ) for item in (items if items is not None else records(selected))]
