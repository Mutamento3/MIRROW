"""Attach a durable cooperation without holding the Wander tick or chat lock."""
from .event_handlers import BaseEventHandler


class WorkshopVisitHandler(BaseEventHandler):
    async def visit_once(self, activity_id, purpose='', *, entry=None):
        from workshop.service import get_service
        from workshop.residency_store import attach, ACTIVE
        from workshop.residency import result, schedule
        from session_manager import get_global_session_manager
        invocation = {'source':'wander_plan','activity_purpose':purpose}
        invocation['session_id'] = await get_global_session_manager().get_current_session_id()
        if entry:
            invocation['workshop_entry'] = entry
        service = get_service()
        record = attach(service, activity_id, invocation)
        if record['state'] not in ACTIVE:
            return result(record)
        schedule(service)
        return {'status':'waiting','message':'工作坊合作已接续；等待制作时不占聊天，真实交付或卡点回来后继续。',
                'collaboration_id':record['id'],'job_id':record['job_id']}

    async def handle(self, event):
        return await self.visit_once(event.event_id)
