"""Local recognition rules shared by social channels, not account authority.

Channels retain their stable account scope and persistence. Only an explicit
local binding supplies a person; nicknames, household links and model output
cannot create that binding. Frozen source records are resolved as frozen.
"""
from __future__ import annotations

EXTERNAL_PERSON_SOURCES = frozenset({'social_feed'})


def entity_binding(entity_id, known, *, blocked=()):
    if not isinstance(entity_id, str):
        status = 'invalid'
    elif not entity_id:
        status = 'unbound'
    elif entity_id in blocked:
        status = 'forbidden'
    elif entity_id not in known:
        status = 'missing_entity'
    else:
        return {'entity_id': entity_id, 'entity_name': known[entity_id]['name'], 'status': 'bound'}
    return {'entity_id': '', 'entity_name': '', 'status': status}


def require_entity(entity_id, known, *, blocked=()):
    result = entity_binding(entity_id, known, blocked=blocked)
    if result['status'] not in {'bound', 'unbound'}:
        raise ValueError('请选择已登记且适用于本通道的实体')
    return result


def resolve_profiles(address, own, linked_address, linked, known):
    """Share only a confirmed person link; relationship notes remain local.

    Partial local profiles no longer hide a linked primary. Conflicting or
    deleted bindings remain visible instead of silently choosing either person.
    """
    direct = entity_binding(own.get('primary_entity_id', ''), known, blocked={'human', 'k'})
    inherited = entity_binding(linked.get('primary_entity_id', ''), known, blocked={'human', 'k'})
    resolved = direct
    source = address
    if direct['status'] == 'unbound' and linked_address:
        resolved, source = inherited, linked_address
    elif (direct['status'] == inherited['status'] == 'bound'
          and direct['entity_id'] != inherited['entity_id'] and linked_address):
        resolved = {'entity_id': '', 'entity_name': '', 'status': 'conflict'}
    # Existing empty profiles may inherit the whole linked profile. Otherwise
    # local annotations retain their scope, even when the primary is inherited.
    annotations = own if any(own.values()) else linked
    primary = resolved['entity_id']
    related = annotations.get('related_entity_ids', [])
    related = related if isinstance(related, list) else []
    profile = {'primary_entity_id': primary,
               'related_entity_ids': list(dict.fromkeys(x for x in related
                   if isinstance(x, str) and x in known and x != primary)),
               'relationship_context': str(annotations.get('relationship_context') or '')}
    return {'profile': profile, 'status': resolved['status'],
            'entity_name': resolved['entity_name'], 'binding_source': source,
            'linked_connection_id': linked_address,
            'stored_primary_entity_id': own.get('primary_entity_id', ''),
            'linked_primary_entity_id': linked.get('primary_entity_id', '') if linked_address else ''}
