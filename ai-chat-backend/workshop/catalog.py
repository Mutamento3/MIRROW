"""A public artifact catalog, independently paged from work-order rooms."""
import json


def public_works(service, offset=0, *, private_offset=0):
    from .cabinet import private_archives
    return {**works(service, offset, audience='public'),
            'private_archives':private_archives(service.store, private_offset)}


def works(service, offset=0, *, audience='k'):
    from .cabinet import archived_ids, summary, view_stats
    archived = archived_ids(service.store)
    offset = max(0, int(offset))
    with service.store.db() as db:
        where = "j.deleted=0" + (" AND j.visibility='public'" if audience == 'public' else '')
        total = db.execute('SELECT count(*) FROM artifacts a JOIN jobs j ON j.id=a.job_id WHERE '+where).fetchone()[0]
        rows = [dict(row) for row in db.execute('SELECT a.*,j.title AS job_title,j.visibility,j.revision AS current_revision,j.state AS job_state '
            'FROM artifacts a JOIN jobs j ON j.id=a.job_id WHERE '+where+' ORDER BY a.created_at DESC,a.id DESC LIMIT 50 OFFSET ?', (offset,))]
        for row in rows:
            row.update(view_stats(db, row['id']))
            row['job_archived'] = row['job_id'] in archived
            row['is_current'] = row['revision'] == row.pop('current_revision')
            row['accepted_by_k'] = any(json.loads(receipt['body']).get('artifact_id') == row['id']
                for receipt in db.execute("SELECT body FROM receipts WHERE job_id=? AND kind='accepted_work'", (row['job_id'],)))
            row['verification'] = None
            for receipt in db.execute("SELECT body,created_at FROM receipts WHERE job_id=? AND kind='inspected_browser' ORDER BY seq DESC", (row['job_id'],)):
                body = json.loads(receipt['body'])
                if body.get('artifact_id') == row['id']:
                    row['verification'] = {'status':body.get('status','preview_only'),
                        'actions_count':len(body.get('checks') or []), 'checked_at':receipt['created_at']}
                    break
    from .quality import latest
    for row in rows:
        from .presentation import kind
        row['presentation_kind'] = kind(row)
        row['quality'] = latest(service.store, row['id'])
        row['lightweight_ready'] = (row['quality'] or {}).get('status') == 'skipped'
    result = {'artifacts':rows, 'total':total, 'offset':offset, 'has_more':offset+len(rows)<total,
              'visits':summary(service.store)}
    if audience == 'k':
        from .collection_inventory import inventory
        result['private_collections'] = inventory(service.store, offset=offset, limit=50)
    return result
