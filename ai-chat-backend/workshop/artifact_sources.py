"""Read-only, hash-checked public deliveries for subsequent creative work."""
from urllib.parse import parse_qs, urlsplit


def catalog(service, offset=0):
    from .catalog import public_works
    shelf=public_works(service,offset)
    fields=('id','job_id','job_title','revision','name','summary','sha256','accepted_by_k')
    return {'source':'workshop_public_deliveries','artifacts':[
        {**{key:row[key] for key in fields},'source_uri':'workshop://artifacts/'+row['id']}
        for row in shelf['artifacts']],
        'offset':shelf['offset'],'has_more':shelf['has_more'],'total':shelf['total']}


def read(service, uri):
    parsed=urlsplit(uri)
    if parsed.scheme!='workshop' or parsed.fragment:
        raise ValueError('invalid_workshop_source')
    query=parse_qs(parsed.query,strict_parsing=True)
    if parsed.netloc=='works' and not parsed.path and set(query)<= {'offset'}:
        return catalog(service,int(query.get('offset',['0'])[0]))
    if parsed.netloc!='artifacts' or query or len(parsed.path)!=33:
        raise ValueError('invalid_workshop_source')
    artifact_id=parsed.path[1:]
    if any(char not in '0123456789abcdef' for char in artifact_id):
        raise ValueError('invalid_workshop_source')
    artifact=service.artifact(artifact_id)
    from .privacy import require_public
    require_public(service.store.job(artifact['job_id']))
    path=service.artifact_path(artifact)
    if path.suffix.lower() not in ('.html','.htm','.txt','.md','.css','.js','.json','.svg','.py') or path.stat().st_size>2000000:
        raise ValueError('workshop_source_requires_bounded_text')
    return {'source':'workshop_public_delivery','source_uri':uri,'artifact_id':artifact_id,
            'job_id':artifact['job_id'],'revision':artifact['revision'],
            'name':artifact['name'],'sha256':artifact['sha256'],'content':path.read_text(encoding='utf8')}
