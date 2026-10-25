"""Persistent review wait facts; polling changes are not new review reports."""
import json

from .store import now


DESKTOP_REASONS = ('桌面 Codex 正在执行任务，质检等候空闲', '桌面运行状态待核对，质检等候')


def wait_key(reason):
    return 'desktop' if reason in DESKTOP_REASONS else reason


def waiting(store, review, reason):
    key = wait_key(reason)
    with store.db() as db:
        current = db.execute('SELECT * FROM quality_reviews WHERE id=?', (review['id'],)).fetchone()
        if current['reason'] != reason:
            db.execute('UPDATE quality_reviews SET reason=?,updated_at=? WHERE id=?', (reason, now(), review['id']))
        # The attempt has a durable identity. Busy/unknown jitter, restarts and
        # preflight races must not turn the same queue wait into fresh events.
        for row in db.execute("SELECT kind,body FROM receipts WHERE job_id=? AND created_at>=? AND kind IN ('quality_blocked','quality_report')",
                              (review['job_id'], current['created_at'])):
            body = json.loads(row['body'])
            if body.get('artifact_id') != review['artifact_id']:
                continue
            if body.get('report_id') and body['report_id'] != review['id']:
                continue
            if row['kind'] == 'quality_report' and body.get('status') != 'queued':
                continue
            previous_key = body.get('wait_key') or wait_key(body.get('reason', ''))
            # Compatibility for previously saved desktop wait cards without a
            # reason field; exact established message formats only.
            if not previous_key and body.get('summary') in [text+'；空闲后自动续跑，不追加额度。' for text in DESKTOP_REASONS]:
                previous_key = 'desktop'
            if previous_key == key:
                return False
        suffix = '；空闲后自动续跑。' if key == 'desktop' else '；条件恢复后自动续跑。'
        store._event(db, review['job_id'], 'quality_blocked', {
            'artifact_id': review['artifact_id'], 'revision': review['revision'],
            'model': review['model'], 'report_id': review['id'], 'wait_key': key,
            'reason': reason, 'summary': reason+suffix})
    return True
