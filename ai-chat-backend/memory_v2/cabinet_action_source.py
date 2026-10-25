"""Human cabinet visits have a receipt, but intentionally no job identity."""
from datetime import datetime
from collections.abc import Mapping
import json


def action_actors(row):
    if row.get('role') != 'event' or row.get('event_type') != 'workshop_event':
        return ()
    calls = row.get('tool_calls') or []
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except (TypeError, ValueError):
            return ()
    for call in calls if isinstance(calls, list) else []:
        extra = call.get('extra_data') if isinstance(call, Mapping) else None
        card = extra.get('workshop') if isinstance(extra, Mapping) else None
        if (not isinstance(card, Mapping) or call.get('tool') != 'workshop'
                or card.get('kind') != 'cabinet_visited' or card.get('source') != 'human'
                or card.get('job_id') != '' or not isinstance(card.get('receipt_id'), str)
                or not card['receipt_id']
                or row.get('message_id') != 'notification:workshop_event:' + card['receipt_id']
                or not isinstance(card.get('visit_id'), str) or not 1 <= len(card['visit_id']) <= 128
                or type(card.get('visit_count')) is not int or card['visit_count'] <= 0
                or not isinstance(card.get('visited_at'), str)):
            continue
        try:
            datetime.fromisoformat(card['visited_at'])
        except ValueError:
            continue
        return ('human',)
    return ()
