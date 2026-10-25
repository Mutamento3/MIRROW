"""Join two independently reviewed linear continuations without erasing history."""
from .day_settlement_lineage import effective_event_status_sql


def projection_subjects(connection, thread_id):
    """Subjects of original nodes and their explicitly recorded replacements."""
    result = {}
    rows = connection.execute(
        "SELECT e.id,e.subject_id,s.status,s.replacement_event_id FROM thread_events te "
        "JOIN events e ON e.id=te.event_id LEFT JOIN event_status_log s ON s.id=("
        "SELECT x.id FROM event_status_log x WHERE x.event_id=e.id ORDER BY x.created_at DESC,x.id DESC LIMIT 1) "
        "WHERE te.thread_id=?", (thread_id,)).fetchall()
    for row in rows:
        result.setdefault(row['id'], set()).add(row['subject_id'])
        if row['status'] == 'superseded' and row['replacement_event_id']:
            result.setdefault(row['replacement_event_id'], set()).add(row['subject_id'])
    return result


def joinable_nodes(connection, source, target, namespace):
    """Return the target suffix only for a current tail-to-head join."""
    from .store import MemoryV2Store
    if len(source) != 1 or len(target) != 1:
        return ()
    left, right = source[0]['thread_id'], target[0]['thread_id']
    if left == right:
        return ()
    groups = []
    for thread in (left, right):
        rows = connection.execute(
            "SELECT te.event_id,te.sequence_no,e.reported_at,b.source_namespace,b.status,"
            f"{effective_event_status_sql('e')} AS current_status "
            "FROM thread_events te JOIN events e ON e.id=te.event_id "
            "JOIN encoding_batches b ON b.id=e.batch_id WHERE te.thread_id=? ORDER BY te.sequence_no",
            (thread,)).fetchall()
        if not rows or any(r['source_namespace'] != namespace or r['status'] != 'completed'
                           or r['current_status'] != 'active' for r in rows):
            return ()
        if any(len(m := MemoryV2Store._continuity_membership(
                connection, r['event_id'], namespace, 'scene_continuation')) != 1
               or m[0]['thread_id'] != thread for r in rows):
            return ()
        groups.append(rows)
    if (source[0]['sequence_no'] != groups[0][-1]['sequence_no']
            or target[0]['sequence_no'] != groups[1][0]['sequence_no']):
        return ()
    rows = groups[0] + groups[1]
    if len({r['event_id'] for r in rows}) != len(rows):
        return ()
    times = [MemoryV2Store._event_time_key(r['reported_at']) for r in rows]
    if times != sorted(times):
        return ()
    return tuple(r['event_id'] for r in groups[1])


def append_reviewed_thread(connection, source, target, namespace, link, now):
    """Called in the page commit transaction, after independent occurrence review."""
    from .store import MemoryV2Store, ThreadConflictError, _stable_id
    from .materialized_continuation import explicitly_withdrawn_pair
    nodes = joinable_nodes(connection, source, target, namespace)
    if not nodes or explicitly_withdrawn_pair(connection, {
            'from_event_id': link.from_event_id, 'to_event_id': link.to_event_id}, namespace):
        raise ThreadConflictError('reviewed thread join is no longer tail-to-head')
    left, right = source[0]['thread_id'], target[0]['thread_id']
    offset = source[0]['sequence_no'] + 1
    for i, event_id in enumerate(nodes, offset):
        MemoryV2Store._insert_thread_event(connection, left, event_id, i, now)
    source_key = 'reviewed-join:' + _stable_id('link', link.from_event_id, link.to_event_id, link.link_type)
    connection.execute(
        "INSERT INTO event_thread_status_log "
        "(id,thread_id,status,reason_code,replacement_thread_id,source_key,created_at) VALUES (?,?,?,?,?,?,?)",
        (_stable_id('thread_status', right, source_key), right, 'superseded',
         'independently_reviewed_continuation', left, source_key, now))
    return left
