"""Independent, idempotent band receipts using the existing notification store."""
from copy import deepcopy


async def detach_receipts(session_id, source_id, calls, emit):
    output = []
    for index, original in enumerate(calls or []):
        call = deepcopy(original)
        if not isinstance(call, dict) or call.get('tool') != 'band_touch':
            output.append(call)
            continue
        if (call.get('extra_data') or {}).get('band_notice_id'):
            output.append(call)
            continue
        params = call.get('parameters') or {}
        note = params.get('note')
        success = call.get('success') is True
        content = ('✉️ AI 发来一张小纸条：' + note if isinstance(note, str) and note.strip()
                   else '⌚ AI 震了手环一下') if success else '手环操作未成功确认'
        result = await emit(session_id, f'band:{source_id}:{index}', event_type='band_action',
                            content=content, metadata={'items': [call]})
        if result and result.get('message_id'):
            call['extra_data'] = {**(call.get('extra_data') or {}), 'band_notice_id': result['message_id']}
        output.append(call)
    return output
