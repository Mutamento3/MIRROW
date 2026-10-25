"""Planning recall uses recent actual conversation, not a previous reflection."""
import logging


async def material_and_query(session_id, inclination):
    from context_builder.active_day_scene_context import read_active_day_scene_material
    try:
        material = await read_active_day_scene_material(session_id)
    except Exception as exc:
        logging.getLogger(__name__).warning('Planning scene unavailable: %s', type(exc).__name__)
        return None, inclination
    rows = []
    for box in (material or {}).get('boxes', ()):
        for segment in box.get('segments', ()):
            refs = {ref['message_id']: ref for ref in segment.get('source_refs', ())}
            rows.extend(message for message in segment.get('messages', ())
                if message.get('role') == 'user'
                and not refs.get(message['message_id'], {}).get('source_event_type'))
    rows.sort(key=lambda row: row.get('timestamp', ''))
    query = '\n'.join(f"[人类伙伴原话 {row.get('timestamp', '')}] {row['content']}" for row in rows[-3:])
    if inclination:
        query += '\n[AI 上段明确留下的未尽起意] ' + inclination
    return material, query
