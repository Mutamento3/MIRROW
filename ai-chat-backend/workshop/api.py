"""HTTP UI and MCP share one domain service and one event loop."""
from __future__ import annotations

import json
import base64
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from commerce_shared.mcp_protocol import McpJsonRpcDispatcher, McpToolSpec
from .service import get_service

router = APIRouter(prefix='/api/workshop', tags=['workshop'])
ASSETS = Path(__file__).parent / 'assets'
ACTION_SCHEMA = {'type': 'object', 'properties': {
    'action': {'type': 'string', 'enum': ['list', 'room', 'create', 'message', 'edit', 'confirm', 'pause', 'resume', 'reconcile', 'cancel', 'delete', 'inspect', 'accept', 'publish']},
    'job_id': {'type': 'string'}, 'request_id': {'type': 'string'}, 'revision': {'type': 'integer'},
    'plan_version': {'type': 'integer'}, 'artifact_id': {'type': 'string'}, 'offset': {'type': 'integer'},
    'title': {'type': 'string'}, 'text': {'type': 'string'}}, 'required': ['action'], 'additionalProperties': False}
ACTION_SCHEMA['properties']['selector'] = {'type': 'string'}
from .browser import ACTION_SCHEMA as BROWSER_ACTION_SCHEMA
ACTION_SCHEMA['properties']['browser_actions'] = BROWSER_ACTION_SCHEMA
ACTION_SCHEMA['properties']['visibility'] = {'type': 'string', 'enum': ['public', 'private']}
ACTION_SCHEMA['properties']['action']['enum'].append('review')
ACTION_SCHEMA['properties']['action']['enum'].append('answer')
ACTION_SCHEMA['properties']['action']['enum'].append('works')
ACTION_SCHEMA['properties']['action']['enum'].append('share')
ACTION_SCHEMA['properties']['action']['enum'].append('finish')
ACTION_SCHEMA['properties']['disposition']={'type':'string','enum':['share','keep','keep_private']}
ACTION_SCHEMA['properties']['decision_id']={'type':'string'}
ACTION_SCHEMA['properties']['answers']={'type':'array','items':{'type':'string'}}
PUBLIC_SCHEMA = {**ACTION_SCHEMA, 'properties': {key: value for key, value in ACTION_SCHEMA['properties'].items() if key not in ('title', 'plan_version', 'visibility')}}
PUBLIC_SCHEMA['properties']['action'] = {'type': 'string', 'enum': ['list', 'room', 'works', 'inspect', 'contribute']}


def dispatcher():
    from .participation import external_action
    return McpJsonRpcDispatcher(name='mirrow-workshop', version='0.1.0', tools=(
        McpToolSpec('workshop', '桌面工作坊：查询公开工单与作品，contribute 将建议以桌面 Codex 来源记回原工单，需要 request_id 与当前 revision。保密单只显示通用状态，不能读取或补充；派工、公开与确认由 AI 在内部完成。', PUBLIC_SCHEMA),),
        handlers={'workshop': lambda args: external_action(get_service(), args)})


@router.on_event('startup')
async def startup():
    from .invitation import recover
    recover(get_service())
    get_service().start()


@router.on_event('shutdown')
async def shutdown():
    from .invitation import close
    await close()
    try:
        await get_service().project()
    finally:
        await get_service().close()


@router.get('')
async def state(offset: int = 0):
    from .invitation import status
    from .residency_store import status as collaboration_status
    return {**get_service().snapshot(offset, audience='public'), 'visit': status(),
            'collaboration':collaboration_status(get_service())}


@router.post('/invite', status_code=202)
async def invite_visit(request: Request):
    same_machine_ui(request)
    from .invitation import invite
    try:
        body = await request.json() if await request.body() else {}
        if not isinstance(body, dict):
            raise ValueError('邀请参数无效')
        template_id = body.get('template_id')
        if template_id is not None and not isinstance(template_id, str):
            raise ValueError('创作起点参数无效')
        text = body.get('text')
        if text is not None and (not isinstance(text,str) or len(text)>2000):
            raise ValueError('邀请附话须为最多 2000 字的文字')
        options={'continuous':True}
        job_id=body.get('job_id')
        if job_id is not None:
            if not isinstance(job_id,str):raise ValueError('原工单参数无效')
            from .privacy import require_public
            require_public(get_service().store.job(job_id))
            options['job_id']=job_id
        if template_id: options['template_id']=template_id
        if text and text.strip(): options['text']=text.strip()
        return await invite(**options)
    except KeyError:
        raise HTTPException(404, '工单不存在或尚未公开') from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(503, '邀请未确认保存或启动，请检查聊天记录和连接后重试') from None


@router.get('/rooms/{job_id}')
async def room(job_id: str, offset: int = 0, flow_version: int = 1):
    try:
        from .participation import room as public_room
        result = public_room(get_service(), job_id, offset)
        from .budget import summary
        result['job']['budget_summary'] = summary(result['job'], limits_enabled=get_service().store.settings()['budget_limits_enabled'])
        from .flow import project
        result['flow'] = project(get_service().store, job_id, include_archive=flow_version>=2)
        return result
    except KeyError:
        raise HTTPException(404, '工单不存在或尚未公开') from None


@router.get('/works')
async def works(offset: int = 0, private_offset: int = 0):
    from .catalog import public_works
    return public_works(get_service(), offset, private_offset=private_offset)


@router.post('/works/visits')
async def cabinet_visit(request: Request):
    same_machine_ui(request)
    from .cabinet import record_visit
    try:
        body = await request.json()
        result = record_visit(get_service().store, body.get('request_id'), body.get('artifact_ids'))
        get_service().wake.set()
        return result
    except KeyError:
        raise HTTPException(404,'作品不存在或尚未公开') from None
    except (ValueError, AttributeError):
        raise HTTPException(400,'参观记录参数无效') from None


@router.post('/rooms/{job_id}/inspiration')
async def inspiration(job_id: str, request: Request):
    same_machine_ui(request)
    from .participation import contribute
    try:
        body = await request.json()
        return await contribute(get_service(), {**body, 'job_id': job_id}, 'human')
    except KeyError:
        raise HTTPException(404, '工单不存在或尚未公开') from None
    except (ValueError, TypeError):
        raise HTTPException(409, '工单版本已变化或补充格式无效，请刷新后重试') from None


@router.post('/rooms/{job_id}/review')
async def review(job_id: str, request: Request):
    same_machine_ui(request)
    service = get_service()
    try:
        from .privacy import require_public
        require_public(service.store.job(job_id))
        body = await request.json()
        await service.action({**body, 'action':'review', 'job_id':job_id})
        return {'saved':True}
    except KeyError:
        raise HTTPException(404, '工单不存在或尚未公开') from None
    except ValueError as exc:
        detail = str(exc) if str(exc).startswith('质检预算') else '作品版本已变化，请刷新后重试'
        raise HTTPException(409, detail) from None


@router.post('/rooms/{job_id}/retry')
async def retry_execution(job_id: str, request: Request):
    same_machine_ui(request)
    from .runner import AppServerRpcError, AppServerRequestTimeout
    try:
        body=await request.json()
        if type(body.get('revision')) is not int:
            raise ValueError('需要当前工单版本')
        from .recovery import retry
        return await retry(get_service(),job_id,body['revision'])
    except KeyError:
        raise HTTPException(404,'工单不存在或尚未公开') from None
    except (ValueError,AttributeError) as exc:
        raise HTTPException(409,str(exc)) from None
    except (AppServerRpcError,AppServerRequestTimeout):
        raise HTTPException(409,'官方线程尚未完成恢复核对，现场保留；没有重新开工，请稍后重试') from None


def same_machine_ui(request):
    # Existing MIRROW network admission remains authoritative. Keep this human
    # resource channel absent from AI's tool catalog and reject cross-site writes.
    origin = request.headers.get('origin', '')
    host = request.headers.get('host', '').split(':')[0].lower()
    origin_host = urlparse(origin).hostname
    matched = origin_host == host or {origin_host, host} <= {'localhost', '127.0.0.1'}
    # Capacitor's configured HTTP origin (and HTTPS default) is accepted by the existing trusted
    # device/network boundary; it is not an arbitrary third-party website.
    if request.headers.get('x-workshop-ui') != 'settings' or not origin or not (matched or origin in ('http://localhost', 'https://localhost')):
        raise HTTPException(403, '资源设置仅由工作坊设置界面提交')


@router.post('/settings')
async def settings(request: Request):
    same_machine_ui(request)
    try:
        service = get_service()
        data = await request.json()
        candidate = {**service.store.settings(), **data}
        from .reasoning import validate_catalog
        for model_key, effort_key in (('model','reasoning_effort'),('review_model','review_reasoning_effort')):
            validate_catalog(candidate[model_key], candidate[effort_key], service.quota.get('reasoning_options', {}))
        cfg = service.store.configure(data)
        service.wake.set()
        return cfg
    except (ValueError, TypeError):
        raise HTTPException(400, '设置值无效') from None


@router.post('/refresh')
async def refresh(request: Request):
    same_machine_ui(request)
    await get_service().refresh()
    return get_service().snapshot(audience='public')


@router.post('/rooms/{job_id}/budget')
async def budget(job_id: str, request: Request):
    same_machine_ui(request)
    try:
        body = await request.json()
        if 'additional_percent' in body:
            from .budget import approve_percent
            await approve_percent(get_service(), job_id, body['additional_percent'], body.get('mode', 'continue'), calibration_exclusive=body.get('calibration_exclusive', False), calibration_delay_seconds=body.get('calibration_delay_seconds', 0))
        else:
            await get_service().approve(job_id, body.get('additional_tokens'), body.get('mode', 'continue'))
        return get_service().snapshot(audience='public')
    except (ValueError, KeyError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) and str(exc).startswith(('需要先校准', '账号剩余')) else '当前工单或增额无效'
        raise HTTPException(400, detail) from None
    except RuntimeError:
        raise HTTPException(503, '额度探测暂不可用，工单保持暂停，请刷新后重试。') from None


@router.post('/mcp')
async def mcp(request: Request):
    if request.headers.get('origin'):
        host = request.headers.get('host', '').split(':')[0].lower()
        if urlparse(request.headers['origin']).hostname != host:
            raise HTTPException(403, 'cross_site_mcp')
    body = await request.body()
    if len(body) > 100000:
        raise HTTPException(413, 'request_too_large')
    try:
        result = await dispatcher().dispatch(json.loads(body))
        structured = ((result or {}).get('result') or {}).get('structuredContent')
        if isinstance(structured, dict) and structured.get('visual_attachment'):
            path = Path(structured.pop('visual_attachment'))
            if isinstance(structured.get('browser_material'), dict):
                structured['browser_material'].pop('screenshot', None)
            result['result']['content'] = [{'type': 'text', 'text': json.dumps(structured, ensure_ascii=False)},
                {'type': 'image', 'mimeType': mimetypes.guess_type(path.name)[0], 'data': base64.b64encode(path.read_bytes()).decode()}]
        return Response(status_code=202) if result is None else result
    except ValueError:
        raise HTTPException(400, 'invalid_json') from None


@router.get('/assets/{name}')
async def asset(name: str):
    if name not in {'astra.gif', 'sol.gif', 'background.png', 'sign.png'}:
        raise HTTPException(404)
    path = ASSETS / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@router.get('/artifacts/{artifact_id}/{mode}')
async def artifact(artifact_id: str, mode: str):
    if mode not in ('preview', 'download', 'view'):
        raise HTTPException(404)
    try:
        service = get_service()
        record = service.artifact(artifact_id)
        from .privacy import require_public
        require_public(service.store.job(record['job_id']))
        path = service.artifact_path(record)
    except (KeyError, ValueError):
        raise HTTPException(404, '作品不存在或版本校验失败') from None
    if mode == 'view':
        from .viewer import page
        document, headers = page(record,path,service.store.job(record['job_id'])['title'])
        return Response(document,media_type='text/html',headers=headers)
    from .preview import HEADERS
    headers = HEADERS
    if mode == 'preview' and path.suffix.lower() in ('.html', '.htm', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.txt', '.md'):
        return FileResponse(path, headers=headers)
    return FileResponse(path, filename=record['name'], headers=headers)


@router.get('/runtime/{artifact_id}/mood')
async def artifact_mood(artifact_id: str):
    from .runtime import supports_mood, project_mood
    from .privacy import require_public
    from affect.service import get_affect_service
    try:
        service = get_service()
        record = service.artifact(artifact_id)
        require_public(service.store.job(record['job_id']))
        if not supports_mood(service.artifact_path(record)):
            raise KeyError(artifact_id)
    except (KeyError, ValueError, OSError, UnicodeError):
        raise HTTPException(404, '作品未接入心情通道') from None
    return Response(json.dumps(project_mood(get_affect_service()), ensure_ascii=False), media_type='application/json', headers={'Cache-Control':'no-store'})
