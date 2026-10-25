"""Reserved local entry points; no upstream business implementation is shipped."""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=['optional-home-interfaces'])


@router.get('/api/lounge/status')
@router.get('/api/home-board/status')
def status():
    return {'available': False, 'configured': False, 'reason': 'optional_adapter_not_configured'}


@router.post('/api/lounge/visit')
@router.post('/api/home-board/post')
def unavailable():
    raise HTTPException(503, 'optional_adapter_not_configured')
