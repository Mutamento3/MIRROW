"""One opt-in synthetic provider check. Never reads production conversations."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory


async def run():
    from event_chronicle import EventChronicleDB
    from .conversation_source import ConversationSource
    from .experiment import _encode_plan
    from .scene_planner import SceneReplayPlanner
    from .scene_digest_store import SceneDigestStore
    from .scene_digest_runtime import digest_day
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        authority = EventChronicleDB(root / "conversation.db")
        day = "2026-09-30"
        session = "session_1234567890123_deadbeef"
        authority.add_conversation_message(day, session, f"{day}T10:00:00+08:00", "user",
            "我今天午饭吃了番茄鸡蛋面。", message_id="synthetic-u1")
        authority.add_conversation_message(day, session, f"{day}T10:01:00+08:00", "assistant",
            "听起来很暖和。你吃完休息一会儿。", message_id="synthetic-k1")
        authority.add_conversation_message(day, session, f"{day}T11:00:00+08:00", "user",
            "刚才那碗面吃完了，现在准备工作。", message_id="synthetic-u2")
        source = ConversationSource(authority.db_path, canonical_sessions_only=True)
        cache = SceneDigestStore(root / "cache.db", create=True)
        async def once(source, plan, base, **kwargs):
            kwargs["max_requests"] = 1
            return await _encode_plan(source, plan, base, **kwargs)
        first = await digest_day(source, cache, day, encoder=once, max_batches=1)
        async def must_not_call(*args, **kwargs):
            raise AssertionError("replay must not call model")
        replay = await digest_day(source, cache, day, encoder=must_not_call)
        return {"synthetic_only": True, "first": first, "replay": replay,
                "source_messages": 3,
                "cached_items": len(cache.projection(SceneReplayPlanner(source), day)["items"])}


async def run_full():
    """Synthetic uninterrupted long box: encode, settle, split, replay."""
    from unittest.mock import patch
    from event_chronicle import EventChronicleDB
    from .conversation_source import ConversationSource
    from .scene_digest_store import SceneDigestStore, cache_path
    from .scene_digest_runtime import digest_day
    from .scene_planner import SceneReplayPlanner
    from .day_backfill_runner import run_backfill_day
    from .scene_material import read_scene_material
    from .experiment import _encode_plan
    with TemporaryDirectory() as temporary, patch("memory_v2.day_projection._configured_paths", return_value=()), \
         patch.dict("os.environ", {"MIRROW_MEMORY_V2_SCENE_START_DATE": "2026-09-30"}):
        root = Path(temporary)
        authority = EventChronicleDB(root / "conversation.db")
        day = "2026-09-30"
        rows = (("user", "中午吃了番茄鸡蛋面，第一次加了很多胡椒，味道太冲了。"),
                ("assistant", "这次的胡椒有点多，下次可以少放一点。"),
                ("user", "换个话题，我今天的项目评审过了！之前卡住的导出功能修好了。"),
                ("assistant", "导出功能终于过评审了，这件事值得庆祝。"),
                ("user", "晚上我在游戏里搭了个蓝色小屋，屋顶是玻璃的，不是在聊工作的项目。"),
                ("assistant", "玻璃屋顶的蓝色小屋，会很亮。"))
        for n, (role, content) in enumerate(rows):
            authority.add_conversation_message(day, "session_1234567890123_deadbeef",
                f"{day}T10:{n:02d}:00+08:00", role, content, message_id=f"synthetic-{n}")
        source = ConversationSource(authority.db_path, canonical_sessions_only=True)
        cache = SceneDigestStore(cache_path(source), create=True)
        async def once(source, plan, base, **kwargs):
            kwargs["max_requests"] = 1
            return await _encode_plan(source, plan, base, **kwargs)
        digest = await digest_day(source, cache, day, completed_before="2026-10-01", encoder=once)
        if digest["status"] != "complete":
            return {"synthetic_only": True, "digest": digest, "status": "encoding_failed"}
        planner = SceneReplayPlanner(source)
        first = await run_backfill_day(source, planner, day, work_db_path=root / "memory.db")
        replay = await run_backfill_day(source, planner, day, work_db_path=root / "memory.db")
        material = read_scene_material(source, day, completed_before="2026-10-01", cache=cache,
                                       memory_paths=(root / "memory.db",))
        segments = [segment for box in material["boxes"] for segment in box["segments"]]
        return {"synthetic_only": True, "digest": digest, "first": first, "replay": replay,
                "source_count": material["source_count"], "time_boxes": material['time_box_count'],
                "semantic_segments": len(segments), "stages": [s["stage"] for s in segments],
                "summaries": [s["summary"] for s in segments]}


async def run_reconstruction():
    """Two-call-budget check: synthetic cached impressions, real raw regrouping."""
    from unittest.mock import patch
    from dataclasses import replace
    from event_chronicle import EventChronicleDB
    from .conversation_source import ConversationSource
    from .encoding import ParsedEncoding
    from .models import EventDraft
    from .scene_digest_store import SceneDigestStore
    from .scene_digest_runtime import digest_day
    from .scene_reconstruction import reconstruct_day
    from .scene_material import read_scene_material
    with TemporaryDirectory() as temporary, patch.dict('os.environ', {
            'MIRROW_MEMORY_V2_SCENE_START_DATE': '2026-09-30'}):
        root = Path(temporary)
        authority = EventChronicleDB(root / 'conversation.db')
        day, session = '2026-09-30', 'session_1234567890123_deadbeef'
        rows = [('10:00', 'user', '午饭吃番茄鸡蛋面，第一次胡椒放多了，味道太冲。'),
                ('10:01', 'assistant', '下次少加些胡椒。'),
                ('10:02', 'user', '换个话题，项目评审过了，卡住的导出功能修好了。'),
                ('10:03', 'assistant', '导出功能过评审，值得庆祝。'),
                ('10:04', 'user', '游戏里搭了蓝色小屋，屋顶是玻璃的，不是工作项目。'),
                ('10:05', 'assistant', '玻璃屋顶会很亮。'),
                ('10:40', 'assistant', '刚刚说的游戏蓝色小屋，屋顶要不要加灯？'),
                ('10:41', 'user', '接着你刚才的建议，给游戏小屋加两盏灯。')]
        for n, (clock, role, content) in enumerate(rows):
            authority.add_conversation_message(day, session, f'{day}T{clock}:00+08:00', role,
                                               content, message_id=f'synthetic-{n}')
        source = ConversationSource(authority.db_path, canonical_sessions_only=True)
        cache = SceneDigestStore(root / 'cache.db', create=True)
        async def seed(source, plan, base, **kwargs):
            messages = source.read_message_ids(tuple(ref.message_id for ref in plan.batch_sources))
            event = EventDraft(ordinal=0, subject_id='human', event_type='conversation_moment',
                summary=('午饭胡椒太多，随后工作评审通过，又聊游戏蓝色小屋玻璃屋顶。'
                         if len(messages) > 1 else '人类伙伴接着 AI 的建议给游戏小屋加灯。'),
                reported_at=messages[0].timestamp, active_date=day, calendar_date=day,
                participant_ids=('human', 'k'), sources=tuple(row.as_source_ref() for row in messages))
            return {'request_count': 0}, ParsedEncoding(events=(event,))
        await digest_day(source, cache, day, completed_before='2026-10-01', encoder=seed)
        first = await reconstruct_day(source, day, completed_before='2026-10-01', cache=cache)
        async def no_call(**kwargs):
            raise AssertionError('cached replay called model')
        replay = await reconstruct_day(source, day, completed_before='2026-10-01',
                                       cache=cache, model_call=no_call)
        material = read_scene_material(source, day, completed_before='2026-10-01', cache=cache, memory_paths=())
        return {'synthetic_only': True, 'first': first, 'replay': replay,
                'time_boxes': material['time_box_count'], 'semantic_boxes': len(material['boxes']),
                'source_count': material['source_count'], 'reconstruction': material['reconstruction'],
                'summaries': [segment['summary'] for box in material['boxes'] for segment in box['segments']],
                'parent_counts': [len(box.get('parent_box_ids', [])) for box in material['boxes']]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(asyncio.run(run()), ensure_ascii=False))


if __name__ == "__main__":
    main()
