"""Offline regression checks for the October public-module synchronization."""
import asyncio
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from memory_v2.recall import RecallQuery
from memory_v2.recall_routing import RecallRoutingHints, apply_recall_routing_hints
from silicon_perception.analysis.anomaly_detector import DataSnapshot
from silicon_perception.analysis.public_place import snapshot_place_observation
from wander_manager import xiaohongshu_adb as adb
from wander_manager.device_lease import DeviceLeaseManager
from wander_manager.xiaohongshu_handler import BrowseXiaohongshuHandler


def test_recall_routing_preserves_extensions_and_explicit_filters():
    @dataclass(frozen=True)
    class ExtendedQuery(RecallQuery):
        synthetic_extension: str = 'keep-me'
    query = ExtendedQuery(text='synthetic', date_from='2026-10-01', date_to='2026-10-02',
                          subject_ids=('human',), exclude_message_ids=('m1',))
    hints = RecallRoutingHints(needs_history=True, needs_source_detail=False,
                               date_from='2026-10-10', date_to='2026-10-11', sort_mode='latest')
    result = apply_recall_routing_hints(query, hints)
    assert type(result) is ExtendedQuery
    for item in fields(query):
        if item.name != 'sort_mode':
            assert getattr(result, item.name) == getattr(query, item.name)
    assert result.sort_mode == 'latest'


def test_place_inference_requires_fresh_place_and_wifi():
    now = datetime.now().astimezone()
    fresh = now.isoformat()
    old = (now-timedelta(hours=2)).isoformat()
    snap = DataSnapshot(location_place_name='示例地点', location_environment='indoor',
                        wifi_connected=True, wifi_is_home=False,
                        field_ts={'gps_place': fresh, 'wifi': fresh})
    assert '推测' in snapshot_place_observation(snap)
    snap.field_ts['wifi'] = old
    assert '推测' not in snapshot_place_observation(snap)
    assert '登记环境' in snapshot_place_observation(snap)
    snap.field_ts['gps_place'] = old
    assert snapshot_place_observation(snap) == ''
    snap.field_ts = {'gps_place': fresh, 'wifi': fresh}
    snap.wifi_is_home = True
    assert snapshot_place_observation(snap) == ''


def test_cancelled_adb_command_keeps_lease_until_process_finishes(monkeypatch):
    started, finish = threading.Event(), threading.Event()
    def fake_runner(argv, timeout):
        started.set()
        assert finish.wait(3)
        return adb.AdbCommandResult()
    monkeypatch.setattr(adb, '_default_runner', fake_runner)
    leases = DeviceLeaseManager(memory_only=True)
    source = adb.AdbXiaohongshuSource(lease_manager=leases)
    async def operation(**kwargs):
        return await source._invoke(('adb', 'devices'))
    source._fetch_one = operation
    async def run():
        task = asyncio.create_task(source.fetch_one())
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert leases.busy and not task.done()
        finish.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not leases.busy
    try:
        asyncio.run(run())
    finally:
        finish.set()


def test_profile_material_keeps_sources_without_raw_frames_or_invented_feelings():
    task = {'operation': 'profile', 'query': 'Example＆Agent', 'count': 1}
    source = SimpleNamespace(fetch_task=AsyncMock(return_value={
        'status': 'success', 'source_id': 'synthetic-post', 'article_text': '完整来源正文尾部',
        'frames': [{'label': 'image_1', 'screenshot_base64': 'transient-frame', 'ui_text': '真实可见文本'}],
        'cursor_after': {'read_count': 1}, 'coverage': {'text': 'partial', 'images': 'partial'}}))
    vision = AsyncMock(return_value={'content': '{"summary":"客观画面","reflection":"不应当作主模型感想"}'})
    handler = BrowseXiaohongshuHandler(device_source=source, vision_func=vision)
    value = asyncio.run(handler.fetch_task_post(task=task))
    assert value['status'] == 'success' and value['cursor_after']['read_count'] == 1
    assert value['article_text'].endswith('正文尾部') and value['reflection'] == ''
    assert 'transient-frame' not in str(value)
    assert value['coverage']['text'] == 'partial'
    vision.side_effect = RuntimeError('synthetic vision unavailable')
    failed = asyncio.run(handler.fetch_task_post(task=task))
    assert failed['status'] == 'vision_unavailable'
    assert 'cursor_after' not in failed and 'transient-frame' not in str(failed)


def test_completed_profile_and_unsupported_continuation_do_not_touch_device():
    source = adb.AdbXiaohongshuSource(lease_manager=DeviceLeaseManager(memory_only=True))
    async def run():
        value = await source.fetch_task(task={'operation':'profile','query':'Example','count':1},
                                        cursor={'read_count':1})
        assert value['status'] == 'profile_target_reached'
        value = await source.fetch_task(task={'operation':'continue','task_id':'synthetic-id'})
        assert value['status'] == 'resume_requires_runtime'
    asyncio.run(run())
