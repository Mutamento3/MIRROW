"""Small local opportunity facts, not recommendations or remote probes."""
import json
import logging
from datetime import datetime
logger = logging.getLogger(__name__)

def facts(store):
    result = {}
    from .cedartoy_client import GAMES
    from music_system import service as music_service
    music = music_service._service
    result['音乐'] = {'已绑定歌单': [{'名称': row['name'], '归属': row['subject'], '本地记录时间': row['imported_at']} for row in music.store.bindings()]} if music is not None else '尚未取得已启动音乐服务的本地目录'
    from .game_invitation import _history
    latest = {}
    for row in _history(store.list_game_history()):
        latest.setdefault((row['game'], row['slot']), row)
    result['游戏'] = {'已接入': list(GAMES.values()), '各游戏存档最近回执': [{key: row[key] for key in ('game', 'slot', 'completed', 'last_played_at', 'checkpoint')} for row in latest.values()]}
    from workshop import service
    if service._service is None:
        result['工作坊'] = '尚未取得已启动工作坊的本地状态'
    else:
        from workshop.wander_entries import describe
        state = describe(service._service)
        result['工作坊'] = {key: state[key] for key in ('summary', 'ongoing_jobs_count', 'needs_k_count', 'displayable_files_count')}
        result['工作坊']['待决定'] = [{key: row[key] for key in ('job_id', 'title', 'state', 'waiting_for', 'pending_k_action')} for row in state['ongoing_jobs'] if row['pending_k_action']]
    return result

def augment_runtime(text, store):
    return '[本次读取的本地机会概况；不是远端在线核验]\n' + json.dumps(facts(store), ensure_ascii=False) + '\n' + text
