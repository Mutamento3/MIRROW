"""Retrieval anchors from the current public request, not workshop boilerplate."""


def query(material):
    parts=[]
    invitation=material.get('invitation') or {}
    request=invitation.get('request_text') or invitation.get('text') or invitation.get('activity_purpose') or invitation.get('creative_request')
    if isinstance(request,str) and request.strip():
        parts.append(request.strip())
    room=material.get('current_room') or {}
    job=room.get('job') or {}
    if job.get('visibility')=='public':
        parts.extend(str(job.get(key) or '').strip() for key in ('title','brief'))
        recent=[row for row in room.get('messages') or [] if row.get('role') in ('k','human')]
        parts.extend(str(row.get('body') or '').strip() for row in recent[-2:])
    elif job:
        parts.append('保密工作坊创作')
    return '\n'.join(dict.fromkeys(part for part in parts if part)) or '小机的创作灵感与共同经历'
