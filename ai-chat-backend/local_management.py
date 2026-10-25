from fastapi import HTTPException, Request
from management_origin import management_origin_allowed

async def local_ui(request: Request):
    import ipaddress
    from shared_state import resolve_allowed_ips
    from network_access import NetworkAccessPolicy
    peer = request.client.host if request.client else ''
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    policy = NetworkAccessPolicy.from_environment(resolve_allowed_ips())
    if not loopback and (not (policy.exact_ips or policy.mobile_lan_mac) or not policy.allows(peer)):
        raise HTTPException(403, '仅允许本机或已登记的个人设备管理本地应用')
    if request.headers.get('x-mirrow-lounge-admin') != '1':
        raise HTTPException(403, '请从 MIRROW 本地管理页面操作')
    origin = request.headers.get('origin')
    if origin and (not management_origin_allowed(origin, str(request.url))):
        raise HTTPException(403, '管理请求来源不匹配')
