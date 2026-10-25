"""系统诊断 / 监控 API 路由 — 从 main.py 提取"""
import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi import HTTPException
from routers.shared_models import SystemStatus, ModelApiStatus
logger = logging.getLogger(__name__)
router = APIRouter(tags=['system'])

@router.get('/api/system/day-end-issues')
async def day_end_issues():
    from day_end_issue_poll import poll
    return await poll.get()

@router.post('/api/system/day-end-issues/{issue_id}/acknowledge')
async def acknowledge_day_end_issue(issue_id: str, body: dict):
    from day_end_issues import acknowledge
    if not await asyncio.to_thread(acknowledge, issue_id, body.get('revision', '')):
        raise HTTPException(409, '问题仍未解决或回执已更新，请刷新后核对。')
    return {'ok': True}

@router.get('/api/whoami')
async def whoami(request: Request):
    """返回客户端 IP，用于验证 Tailscale 连通性和 IP 白名单"""
    from shared_state import get_allowed_ips
    allowed = get_allowed_ips()
    return {'client_ip': request.client.host if request.client else 'unknown', 'allowed': request.client.host in allowed if request.client else False, 'allowed_ips': sorted(allowed)}

@router.post('/api/system/startup')
async def on_app_startup(background_tasks: BackgroundTasks=None):
    """前端触发系统启动初始化（日历事件扫描等）"""
    from calendar_manager.manager import get_global_calendar_manager
    cal_mgr = get_global_calendar_manager()
    try:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        last_date = chronicle.get_last_active_date()
        if last_date:
            last_dt = datetime.strptime(last_date, '%Y-%m-%d')
            days_absent = max(0, (datetime.now() - last_dt).days)
        else:
            days_absent = 0
    except Exception:
        days_absent = 0

    async def _bg_startup_work():
        try:
            await cal_mgr.scan_today_events(days_absent=days_absent)
        except Exception as e:
            logger.warning(f'Startup 日历扫描失败: {e}')
    if background_tasks:
        background_tasks.add_task(_bg_startup_work)
    else:
        asyncio.create_task(_bg_startup_work())
    return {'status': 'ok', 'days_absent': days_absent}

@router.get('/')
async def root():
    return {'message': 'AI Chat Backend Proxy'}

@router.get('/health')
async def health():
    return {'status': 'healthy'}

@router.get('/api/system/task-status')
async def get_task_status():
    """获取后台任务状态（供前端进度条轮询）"""
    from shared_state import get_pending_tasks
    from task_progress_projection import project
    return project(get_pending_tasks())

@router.post('/api/system/task-cancel')
async def cancel_task(payload: dict):
    """终止某个疑似卡死的后台任务（前端「打印日志并终止」/❌按钮）。
    body: {task_id: str, dump_log: bool=true}。dump_log 时先把「聊到这里」之后的日志
    导出到 logs/debug/ 供次日调试。"""
    task_id = (payload or {}).get('task_id', '')
    dump_log = bool((payload or {}).get('dump_log', True))
    if not task_id:
        return {'success': False, 'error': 'missing task_id'}
    from main import cancel_tracked_task
    return await cancel_tracked_task(task_id, dump_log)

@router.get('/api/auto-start/status')
async def get_auto_start_status():
    """获取 MIRROW 开机自启状态"""
    from auto_start_manager import get_all_status
    return get_all_status()

@router.post('/api/auto-start/{component}')
async def set_auto_start(component: str, body: dict):
    """设置/取消开机自启。component: 'mirrow'"""
    from auto_start_manager import enable, disable, ENTRIES
    if component not in ENTRIES:
        return {'ok': False, 'error': f'未知组件: {component}，有效值: mirrow'}
    info = ENTRIES[component]
    enable_flag = body.get('enable', True)
    if enable_flag:
        result = enable(info['name'], info['command'])
    else:
        result = disable(info['name'])
    return {'ok': result['ok'], 'enabled': enable_flag and result['ok'], 'error': result.get('error'), 'component': component, 'name': info['name']}

def _get_behavior_scheduler_status() -> dict:
    """一次采集内存可用状态；读取失败不是已确认不可用。"""
    try:
        from shared_state import get_behavior_scheduler_available
        available = get_behavior_scheduler_available()
        if not isinstance(available, bool):
            raise TypeError('Behavior availability must be a boolean')
        return {'status': 'ok', 'available': available}
    except Exception as exc:
        category = type(exc).__name__
        return {'status': 'error', 'available': None, 'error_type': category if category in {'ImportError', 'TypeError', 'RuntimeError'} else 'Exception'}

def _get_recipe_call_log_observation() -> dict:
    """一次读取日志；有效空记录与观测失败分别报告。"""
    try:
        from context_builder import get_recipe_call_log
        records = get_recipe_call_log()
        if not isinstance(records, dict) or any((not isinstance(key, str) or not isinstance(value, dict) for key, value in records.items())):
            raise TypeError('Recipe call log must be a mapping of records')
        return {'status': 'ok', 'records': records}
    except Exception as exc:
        category = type(exc).__name__
        return {'status': 'error', 'records': None, 'error_type': category if category in {'ImportError', 'TypeError', 'RuntimeError'} else 'Exception'}

@router.get('/recipes')
async def get_recipe_directory():
    """返回全部 Recipe 目录（供监控台总览 tab 展示）。
    含 group/consumer 元数据 + 是否被调用过（via recipe_call_log）。
    """
    try:
        from context_builder import list_recipes
        from context_builder.recipes import get_recipe
        observation = _get_recipe_call_log_observation()
        call_log = observation['records'] or {}
        result = []
        for name in list_recipes():
            r = get_recipe(name)
            if not r:
                continue
            entry = {'name': r.name, 'key': name, 'description': r.description, 'consumer': r.consumer, 'pipeline': r.pipeline, 'section_count': len(r.sections), 'sections': [{'name': s.name, 'ingredient': s.ingredient, 'group': s.group, 'condition': s.condition} for s in r.sections]}
            called = False if observation['status'] == 'ok' else None
            for scenario, rec in call_log.items():
                if rec.get('recipe') == r.name or rec.get('scenario') == r.name:
                    called = True
                    entry['last_call'] = {'timestamp': rec.get('timestamp'), 'total_chars': rec.get('total_chars'), 'total_tokens': rec.get('total_tokens'), 'via_builder': rec.get('via_builder')}
                    break
            entry['called'] = called
            result.append(entry)
        return {'recipes': result, 'recipe_call_log_observation': {k: v for k, v in observation.items() if k != 'records'}}
    except Exception:
        return {'recipes': [], 'error': '配方目录读取失败'}

@router.get('/api/debug/context-inspections')
async def context_inspection_list(request: Request):
    from context_inspection import list_inspections, require_private_inspection
    from fastapi.responses import JSONResponse
    require_private_inspection(request)
    return JSONResponse({'items': list_inspections()}, headers={'Cache-Control': 'no-store'})

@router.get('/api/debug/context-inspections/{call_id}')
async def context_inspection_detail(call_id: str, request: Request):
    from context_inspection import get_inspection, require_private_inspection
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    require_private_inspection(request)
    snapshot = get_inspection(call_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail='此快照已过期或尚未采集')
    return JSONResponse(snapshot, headers={'Cache-Control': 'no-store'})

@router.get('/neurons')
async def get_neuron_status():
    """返回所有神经元的聚合状态 + 迁移统计（供监控台神经元面板）。"""
    try:
        from neuron_registry import get_all_neuron_status
        return get_all_neuron_status()
    except Exception as e:
        return {'neurons': [], 'stats': {'error': str(e)}}

@router.get('/neurons/{neuron_id}/history')
async def get_neuron_history(neuron_id: str, limit: int=20):
    """返回指定神经元的执行历史 trace（含完整输入/思考/输出）。"""
    try:
        from neuron_registry import get_neuron_history as _get_history
        return _get_history(neuron_id, limit)
    except Exception as e:
        return {'neuron_id': neuron_id, 'traces': [], 'error': str(e)}

@router.get('/monitor', response_model=SystemStatus)
async def get_system_monitor():
    """获取系统监控状态（含所有模块健康指标）"""
    from datetime import datetime, timedelta
    import json, re as _re
    import music_monitor
    from time_utils import now_iso
    from vector_embedder import SENTENCE_TRANSFORMERS_AVAILABLE, FAISS_AVAILABLE
    CONTEXT_SCHEDULER_AVAILABLE = True
    from llm_client import DEEPSEEK_API_KEY, DEEPSEEK_API_URL, GLM_API_KEY, GLM_API_URL, SUMMARY_MODEL
    try:
        from silicon_perception.sentinel import get_sentinel
        SENTINEL_AVAILABLE = get_sentinel() is not None
    except ImportError:
        SENTINEL_AVAILABLE = False
    from shared_state import get_task_last_success, get_task_fail_count, get_task_last_error, get_cron_last_success, get_cron_last_error, get_last_reminder_injected_at, get_recently_active_sessions, get_latest_session_id, get_pending_tasks, get_wander_manager, get_scheduler, get_latest_persona_prompt, get_last_wb_detection_date, get_wb_detection_ampm
    timestamp = now_iso()
    backend_status = 'healthy'
    ombre_brain_status = 'retired'
    from memory_v2.context_shadow import warm_context_shadow_from_environment
    memory_health = warm_context_shadow_from_environment()
    sentence_transformer_status = 'available' if SENTENCE_TRANSFORMERS_AVAILABLE else 'not_available'
    vector_index_stats = {'source': 'Memory V2', 'status': memory_health.get('status')}
    try:
        from memory_v2.library import library_status
        current_memory = await asyncio.to_thread(library_status)
        vector_index_stats.update({key: current_memory[key] for key in ('total', 'event_count', 'thread_count', 'group_count')})
    except Exception:
        pass
    try:
        from memory_v2.legacy_archive import get_legacy_archive
        bucket_stats_data = await asyncio.to_thread(get_legacy_archive().observation)
    except Exception:
        bucket_stats_data = {'status': 'unavailable', 'runtime': 'retired'}
    migration_status = {'last_run': 'N/A（迁移已于2026-05-19完成）', 'status': 'completed'}
    model_api_status = ModelApiStatus(deepseek=bool(DEEPSEEK_API_KEY), glm=bool(GLM_API_KEY), summary_model=SUMMARY_MODEL)
    scheduler_jobs = []
    s = get_scheduler()
    if s and s.running:
        for job in s.get_jobs():
            scheduler_jobs.append({'name': job.name, 'next_run': str(job.next_run_time) if job.next_run_time else None, 'trigger': str(job.trigger)})
    scheduler_status = {'running': s is not None and s.running if s else False, 'jobs_count': len(s.get_jobs()) if s else 0, 'jobs': scheduler_jobs}
    behavior_observation = _get_behavior_scheduler_status()
    recipe_observation = _get_recipe_call_log_observation()
    external_services = {'ob_rev_client': 'retired', 'deepseek_api_url': DEEPSEEK_API_URL, 'glm_api_url': GLM_API_URL if GLM_API_URL else 'not_configured', 'context_scheduler_available': CONTEXT_SCHEDULER_AVAILABLE, 'faiss_available': FAISS_AVAILABLE, 'behavior_scheduler_available': behavior_observation['available'], 'behavior_scheduler_observation': behavior_observation}
    context_pipeline = {}
    try:
        from context_scheduler_mcp.context_scheduler import get_pipeline_health
        context_pipeline = get_pipeline_health()
    except ImportError as e:
        logger.warning(f'get_pipeline_health 导入失败: {e}')
    except Exception as e:
        logger.warning(f'get_pipeline_health 调用失败: {e}')
    post_topic_health = {'tasks': {'diary_backfill': {'last_success': get_task_last_success().get('diary_backfill', ''), 'fail_count': get_task_fail_count().get('diary_backfill', 0), 'last_error': get_task_last_error().get('diary_backfill', '')}, 'diary_generation': {'last_success': get_task_last_success().get('diary_generation', ''), 'fail_count': get_task_fail_count().get('diary_generation', 0), 'last_error': get_task_last_error().get('diary_generation', '')}, 'unified_analysis': {'last_success': get_task_last_success().get('unified_analysis', ''), 'fail_count': get_task_fail_count().get('unified_analysis', 0), 'last_error': get_task_last_error().get('unified_analysis', '')}, 'persona_evolution': {'last_success': get_task_last_success().get('persona_evolution', ''), 'fail_count': get_task_fail_count().get('persona_evolution', 0), 'last_error': get_task_last_error().get('persona_evolution', '')}}, 'last_wb_detection_date': get_last_wb_detection_date(), 'lastget_wb_detection_ampm()': get_wb_detection_ampm(), 'current_pending': {k: {'status': v.get('status'), 'started_at': v.get('started_at', '')} for k, v in get_pending_tasks().items()} if get_pending_tasks() else {}}
    diary_health = {}
    try:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        last_entry = chronicle.get_latest_diary_entry()
        last_entry_date = last_entry.date if last_entry else None
        today = datetime.now().strftime('%Y-%m-%d')
        last_active = chronicle.get_last_active_date(today)
        gap_days = 0
        if last_entry_date and last_active:
            try:
                entry_dt = datetime.strptime(last_entry_date, '%Y-%m-%d')
                active_dt = datetime.strptime(last_active, '%Y-%m-%d')
                gap_days = (active_dt - entry_dt).days
            except Exception:
                logger.exception('unexpected error in get_system_monitor')
                pass
        diary_health = {'last_entry_date': last_entry_date, 'last_active_date': last_active, 'gap_days': max(0, gap_days), 'status': 'ok' if gap_days <= 1 else 'gap_detected'}
    except Exception as e:
        diary_health = {'error': str(e)[:100]}
    cron_health = {'daily_maintenance': {'last_success': get_cron_last_success().get('daily_maintenance', ''), 'last_error': get_cron_last_error().get('daily_maintenance', '')}, 'task_check': {'last_success': get_cron_last_success().get('task_check', ''), 'last_error': get_cron_last_error().get('task_check', '')}}
    reminders_health = {}
    try:
        from task_scheduler.database import _get_conn as get_task_conn
        pending_count = 0
        try:
            conn = get_task_conn()
            row = conn.execute('SELECT COUNT(*) FROM pending_reminders WHERE resolved=0').fetchone()
            pending_count = row[0] if row else 0
            conn.close()
        except Exception:
            logger.exception('unexpected error in get_system_monitor')
            pass
        reminders_health = {'pending_count': pending_count, 'last_injected_at': get_last_reminder_injected_at()}
    except Exception as e:
        reminders_health = {'error': str(e)[:100]}
    try:
        sentinel = get_sentinel() if SENTINEL_AVAILABLE and get_sentinel else None
    except Exception:
        sentinel = None
    wm = get_wander_manager()
    components_health = {'wander_running': wm is not None and getattr(getattr(wm, '_wander_creator', None), 'is_running', False), 'sentinel_running': sentinel._running if sentinel else False, 'music_monitor_running': music_monitor.music_monitor is not None and getattr(music_monitor.music_monitor, '_monitoring', False) if music_monitor._MONITOR_DEPS_AVAILABLE else False, 'affect': {}}
    try:
        from affect import get_affect_service
        _affect = get_affect_service()
        _settings = _affect.settings()
        components_health['affect'] = {'initialized': True, 'engine_mode': str(_settings.get('engine_mode', 'enabled')), 'self_enabled': bool(_settings.get('self_context_enabled', True)), 'recognition_enabled': bool(_settings.get('resonance_recognition_enabled', True)), 'channel_enabled': bool(_settings.get('resonance_channel_enabled', False))}
    except Exception:
        components_health['affect'] = {'error': 'unavailable'}
    sessions_health = {'active_sessions_count': len(get_recently_active_sessions()) if get_recently_active_sessions() else 0, 'latest_session_id': get_latest_session_id() or ''}
    try:
        from summary import topic_state_machine
        active_topic_count = 0
        active_topics = []
        active_sessions = get_recently_active_sessions()
        for sid in list(active_sessions):
            try:
                status = await topic_state_machine.get_topic_status(sid)
                if status.get('is_active', False):
                    active_topic_count += 1
                    active_topics.append({'session_id': sid[:16], 'start_time': status.get('start_time', ''), 'last_activity_time': status.get('last_activity_time', ''), 'message_count': status.get('message_count', 0), 'round_count': status.get('round_count', 0)})
            except Exception as e:
                logger.warning(f'获取会话 {sid[:16]} 话题状态失败: {e}')
        sessions_health['active_topic_count'] = active_topic_count
        sessions_health['active_topics'] = active_topics
    except Exception as e:
        logger.exception('会话健康检查失败')
        sessions_health['active_topic_count'] = 0
    keyword_pool_stats = {}
    try:
        from wander_manager.keyword_pool import get_keyword_pool
        pool = get_keyword_pool()
        entries = pool.get_all_words()
        sentiment_dist = {'positive': 0, 'neutral': 0, 'negative': 0}
        for e in entries:
            s = e.sentiment or 'neutral'
            sentiment_dist[s] = sentiment_dist.get(s, 0) + 1
        most_used = sorted(entries, key=lambda e: e.selected_count, reverse=True)[:10]
        keyword_pool_stats = {'pool_size': len(entries), 'max_pool_size': pool.MAX_POOL_SIZE, 'daily_additions_today': getattr(pool, '_today_added', 0), 'max_daily_additions': pool.MAX_DAILY_ADDITIONS, 'sentiment_distribution': sentiment_dist, 'most_used': [{'word': e.word, 'count': e.selected_count, 'sentiment': e.sentiment} for e in most_used]}
    except Exception as e:
        keyword_pool_stats = {'error': str(e)[:100]}
    token_stats = {}
    try:
        import os as _os
        log_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'logs', 'token_usage.log')
        if _os.path.exists(log_path):
            today = datetime.now().strftime('%Y-%m-%d')
            today_calls = 0
            today_input = 0
            today_output = 0
            today_cache_hit = 0
            today_cache_miss = 0
            today_cost = 0.0
            caller_counts = {}
            model_breakdown = {}
            with open(log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        ts = rec.get('ts', '')
                        if ts.startswith(today):
                            today_calls += 1
                            today_input += max(0, rec.get('prompt', 0))
                            today_output += max(0, rec.get('completion', 0))
                            ch = max(0, rec.get('cache_hit', 0))
                            cm = max(0, rec.get('cache_miss', 0))
                            today_cache_hit += ch
                            today_cache_miss += cm
                            cost = rec.get('cost')
                            if cost is None:
                                from token_tracker import estimate_cost_yuan
                                cost = estimate_cost_yuan(rec.get('model', ''), ch, cm, max(0, rec.get('completion', 0)), ts)
                            today_cost += cost
                            c = rec.get('caller', 'unknown')
                            caller_counts[c] = caller_counts.get(c, 0) + 1
                            m = rec.get('model', 'unknown')
                            mb = model_breakdown.setdefault(m, {'calls': 0, 'input': 0, 'output': 0, 'cache_hit': 0, 'cache_miss': 0, 'cost': 0.0})
                            mb['calls'] += 1
                            mb['input'] += max(0, rec.get('prompt', 0))
                            mb['output'] += max(0, rec.get('completion', 0))
                            mb['cache_hit'] += ch
                            mb['cache_miss'] += cm
                            mb['cost'] += cost
                    except Exception:
                        pass
            _denom = today_cache_hit + today_cache_miss
            token_stats = {'today_calls': today_calls, 'today_input_tokens': today_input, 'today_output_tokens': today_output, 'today_total_tokens': today_input + today_output, 'today_cache_hit_tokens': today_cache_hit, 'today_cache_miss_tokens': today_cache_miss, 'cache_hit_ratio': round(today_cache_hit / _denom, 4) if _denom > 0 else None, 'est_cost_yuan': round(today_cost, 6), 'caller_breakdown': caller_counts, 'model_breakdown': model_breakdown}
    except Exception as e:
        token_stats = {'error': str(e)[:100]}
    context_snapshot = {}
    try:
        from context_scheduler_mcp.context_scheduler import get_context_snapshot
        context_snapshot = get_context_snapshot()
    except Exception:
        logger.exception('unexpected error in get_system_monitor')
        pass
    keyword_pools = {}
    try:
        trigger_kw = ['之前', '上次', '那件事', '还记得吗', '以前', '曾经', '过去', '记忆', '记得', '昨天', '前天', '上周', '上个月', '去年', '前几天', '前阵子', '前一阵', '最近', '那天', '有一次', '某天']
        wb_kw = []
        wb_count = 0
        try:
            from world_book import get_global_world_book
            wb = get_global_world_book()
            if wb._loaded:
                wb_kw = wb.get_all_keywords()
                wb_count = len(wb._entries)
        except Exception:
            logger.exception('unexpected error in get_system_monitor')
            pass
        keyword_pools = {'trigger_keywords': sorted(trigger_kw), 'world_book_keywords': sorted(wb_kw), 'world_book_entry_count': wb_count}
    except Exception as e:
        keyword_pools = {'error': str(e)[:100]}
    return SystemStatus(timestamp=timestamp, backend_status=backend_status, ombre_brain_status=ombre_brain_status, sentence_transformer_status=sentence_transformer_status, vector_index_stats=vector_index_stats, bucket_stats=bucket_stats_data, migration_status=migration_status, model_api_status=model_api_status, scheduler_status=scheduler_status, external_services=external_services, context_pipeline=context_pipeline, post_topic_health=post_topic_health, diary_health=diary_health, cron_health=cron_health, memory_health=memory_health, reminders_health=reminders_health, components_health=components_health, sessions_health=sessions_health, keyword_pool_stats=keyword_pool_stats, token_stats=token_stats, context_snapshot=context_snapshot, recipe_call_log=recipe_observation['records'] or {}, recipe_call_log_observation={k: v for k, v in recipe_observation.items() if k != 'records'}, keyword_pools=keyword_pools, neuron_summary=_get_neuron_summary())

def _get_neuron_summary() -> dict:
    """获取神经元摘要（供 /monitor 端点）。"""
    try:
        from neuron_registry import get_neuron_summary
        return get_neuron_summary()
    except Exception:
        return {}
