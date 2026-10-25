"""A small archive index for AI; general planning receives only its count."""

SOURCE = '当前已验收保密工单与本人分享回执；不读取房间正文'
SCOPE = '已完成、尚未分享的保密收藏；不是待确认、待验收或必须分享的任务'
WHERE = """j.deleted=0 AND j.state='accepted' AND j.visibility='private'
    AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.job_id=j.id
        AND r.kind='artifact_shared' AND json_extract(r.body,'$.source')='k'
        AND json_extract(r.body,'$.revision')=j.revision)"""


def inventory(store, *, audience='k', offset=0, limit=20):
    offset = max(0, int(offset))
    limit = max(1, min(50, int(limit)))
    with store.db() as db:
        total = db.execute('SELECT count(*) FROM jobs j WHERE '+WHERE).fetchone()[0]
        result = {'total':total, 'source':SOURCE, 'scope':SCOPE}
        if audience != 'k':
            return result
        rows = [dict(row) for row in db.execute(
            'SELECT j.id,j.title,j.revision,j.state,j.visibility,j.created_at,'
            "(SELECT max(r.created_at) FROM receipts r WHERE r.job_id=j.id AND r.kind='accepted_work' "
            "AND json_extract(r.body,'$.revision')=j.revision) AS accepted_at "
            'FROM jobs j WHERE '+WHERE+
            ' ORDER BY coalesce(accepted_at,j.updated_at) DESC,j.id DESC LIMIT ? OFFSET ?', (limit,offset))]
    for row in rows:
        row['sharing_status'] = 'not_shared'
    return {**result, 'items':rows, 'offset':offset,
            'has_more':offset+len(rows)<total,
            'next_offset':offset+len(rows) if offset+len(rows)<total else None}
