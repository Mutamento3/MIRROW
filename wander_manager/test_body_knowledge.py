"""Public body projection must not claim private integrations or mutate state."""
from unittest.mock import patch

from wander_manager.body_knowledge import body_overview, get_body_knowledge
from wander_manager.event_catalog import EVENT_CATALOG


def test_body_is_deterministic_and_uses_event_catalog():
    first = get_body_knowledge()
    assert first == get_body_knowledge()
    assert first['event_names'] == [entry.display_name for entry in EVENT_CATALOG.values()]
    assert '不等于已启用' in body_overview()
    first['records'][0]['details'] = 'caller mutation'
    assert first != get_body_knowledge()


def test_body_retains_complete_facts_and_excludes_tool_protocol():
    record = dict(domain='test', capability='test', state='optional', details='fact' * 3000,
                  source='synthetic', freshness='per call', limit='not connected')
    with patch('wander_manager.body_knowledge.get_capability_records',
               return_value=(record, {**record, 'scope': 'tool', 'capability': 'hidden-schema'})):
        body = get_body_knowledge()
        assert record['details'] in body['overview']
        assert 'hidden-schema' not in body['overview']
        assert len(body['records']) == 1
