"""Local read-only history. No store initialization, phone or model calls."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from xhs_publish.store import ROOT
from .context import brief, capture, LABELS


def _records(path, table, ids=None, *, date=''):
    if table not in {'attention', 'interactions', 'replies', 'image_reads'}: raise ValueError('invalid_archive_table')
    path = Path(path)
    if not path.is_file(): return []
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            if ids is None:
                where = " WHERE substr(json_extract(record,'$.created_at'),1,10)=? OR substr(json_extract(record,'$.read_at'),1,10)=?" if date else ''
                rows = db.execute(f'SELECT record FROM {table}'+where+' ORDER BY rowid DESC LIMIT 1200',
                                  (date,date) if date else ()).fetchall()
            else:
                ids = sorted(x for x in ids if isinstance(x,str) and re.fullmatch('[0-9a-f]{64}',x))
                if not ids: return []
                key = 'batch_id' if table == 'attention' else 'id'
                rows = db.execute(f'SELECT record FROM {table} WHERE {key} IN ('+
                                  ','.join('?' for _ in ids)+')', ids).fetchall()
        return [json.loads(row[0]) for row in rows]
    except (sqlite3.Error, ValueError, OSError): return []


def _payload(record):
    if isinstance(record.get('result'), dict): return record['result']
    return {'status': record.get('state'), 'batch_id': record.get('batch_id'),
            'materials': record.get('materials') or [],
            'k_received': record.get('state') in {'model_returned', 'decided', 'executing', 'completed'},
            'xhs_interactions': record.get('action_results') or [],
            'reflection': (record.get('decision') or {}).get('reflection') or ''}


def _detail_text(sources, receipts):
    lines=['[小红书本地历史来源；非新通知，未操作手机]']
    notes, comments, replies, feelings = set(), set(), set(), set()
    current={row['id']:row for row in receipts}
    image_ids=set()
    def images(reading, *, later=False, received=False):
        if not reading or reading.get('id') in image_ids:return
        image_ids.add(reading.get('id'))
        lines.append(('后续独立图像读取（不表示AI当时已收到）：' if later else
                      '当次收到的图像观察：' if received else '当次冻结的图像材料（AI接收尚未确认）：')
                     +str(reading.get('created_at') or ''))
        lines.append('图像范围：'+str(reading.get('image_coverage') or '')+'；'+str(reading.get('scope') or ''))
        for page in reading.get('pages') or []:
            lines.append('图'+str(page.get('page'))+'/'+str(page.get('total'))+'；'+str(page.get('state'))
                         +'；来源：'+str(page.get('provider') or '')+'/'+str(page.get('model') or ''))
            if page.get('observation'):lines.append('画面：'+page['observation'])
            if page.get('visible_text'):lines.append('图中文字：'+str(page['visible_text']))
            if page.get('limitations'):lines.append('局限：'+str(page['limitations']))
    def target(item):
        key=(item.get('note_author'),item.get('note_title'),item.get('note_body'))
        if key not in notes:
            notes.add(key)
            lines.append('原帖《'+str(item.get('note_title') or '未标注标题')+'》；作者：'+str(item.get('note_author') or '未核验'))
            lines.append('原帖文字：'+str(item.get('note_body') or ''))
            lines.append('读取范围：'+str(item.get('note_body_scope') or '当时记录的可见文字'))
        comment=(item.get('identity'),item.get('actor_name'),item.get('comment_text'))
        if comment not in comments:
            comments.add(comment)
            lines.append('原评论；'+str(item.get('actor_name') or '作者未核验')+'：'+str(item.get('comment_text') or ''))
            if item.get('actor_account_id'):
                lines.append('评论者公开账号：'+str(item['actor_account_id']))
            if item.get('actor_relationship') == 'human_verified':
                lines.append('评论者身份：已按绑定公开账号核验为人类伙伴。')
            for row in item.get('thread') or []:
                if (row.get('actor_name'),row.get('text')) != (item.get('actor_name'),item.get('comment_text')):
                    lines.append('当时可见评论；'+str(row.get('actor_name') or '')+'：'+str(row.get('text') or ''))
    def reply(receipt):
        if not receipt or receipt.get('id') in replies:return
        replies.add(receipt.get('id'))
        if receipt.get('target'):target(receipt['target'])
        state=receipt.get('state') or 'unknown'
        lines.append('AI当时写的回复：'+str(receipt.get('text') or ''))
        lines.append('当时回执：'+LABELS.get(state,'状态未确认'))
        live=current.get(receipt.get('id'))
        if live and live.get('state') != state:
            lines.append('本次查询的后续回执：'+LABELS.get(live['state'],'状态未确认'))
    for source in sources:
        received=source.get('k_received') is True or source.get('status')=='completed'
        for item in source.get('materials') or []:
            if item.get('target'):target(item['target'])
            images(item.get('image_reading'),received=received)
        incoming=source.get('interaction') or {}
        images(incoming.get('image_reading'),received=received)
        if incoming.get('target'):target(incoming['target'])
        elif incoming.get('incoming'):
            notification=incoming['incoming']
            lines.append('仅发现通知，原帖未读取；'+str(notification.get('actor_name') or '')+'：'+str(notification.get('notification_text') or ''))
        for row in source.get('xhs_interactions') or []:
            if row.get('status') == 'read_without_reply':lines.append('AI当时决定：读后离开，未回复。')
            reply(row.get('xhs_reply'))
        feeling=source.get('reflection') or ''
        if feeling and feeling not in feelings:
            feelings.add(feeling);lines.append('AI当时原述心绪：'+feeling)
    for source in sources:
        for reading in source.get('later_image_readings') or []:images(reading,later=True)
    for row in receipts:reply(row)
    return '\n'.join(lines)


def index(*, date='', limit=20, session_id='', path=None):
    if date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date): raise ValueError('invalid_archive_date')
    path = path or ROOT/'events'/'xhs_interactions.db'
    result, covered_replies, covered_interactions = [], set(), set()
    for record in _records(path, 'attention', date=date):
        if session_id and record.get('session_id') != session_id: continue
        payload = _payload(record)
        refs = capture(payload)['refs']
        covered_replies.update(refs['reply_ids']); covered_interactions.update(refs['interaction_ids'])
        stamp = record.get('created_at') or capture(payload)['date']
        if date and str(stamp)[:10] != date: continue
        result.append({'event_id': 'xhs-attention:'+record['batch_id'], 'date': str(stamp)[:10],
                       'text': brief(payload, timestamp=stamp)})
    for record in _records(path, 'replies', date=date):
        if session_id and record.get('session_id') != session_id: continue
        if record['id'] in covered_replies: continue
        covered_interactions.add(record['interaction_id'])
        stamp = record.get('created_at') or ''
        if date and stamp[:10] != date: continue
        result.append({'event_id': 'xhs-reply:'+record['id'], 'date': stamp[:10],
                       'text': brief({'xhs_reply': record}, timestamp=stamp)})
    for record in _records(path, 'interactions', date=date):
        if session_id and record.get('session_id') != session_id: continue
        if record['id'] in covered_interactions: continue
        stamp = record.get('read_at') or record.get('created_at') or ''
        if date and stamp[:10] != date: continue
        payload = ({'status':'read', 'interaction':record} if record.get('state') == 'read'
                   else {'status':'observed', 'items':[record]})
        result.append({'event_id':'xhs-interaction:'+record['id'], 'date':stamp[:10],
                       'text':brief(payload, timestamp=stamp)})
    result.sort(key=lambda row: row['date'], reverse=True)
    return {'status':'ok','items':result[:max(1,min(int(limit),50))],
            'scope':'local_recorded_interactions_only;bounded_to_1200_per_table_and_50_results'}


def detail(event_id='', *, refs=None, session_id='', path=None):
    path = path or ROOT/'events'/'xhs_interactions.db'
    refs = refs or {}
    prefix, _, identity = str(event_id).partition(':')
    wanted = {'attention': set(), 'interactions': set(), 'replies': set()}
    table = {'xhs-attention':'attention','xhs-interaction':'interactions','xhs-reply':'replies',
             'xhs-interaction-reply':'replies'}.get(prefix)
    if table and re.fullmatch('[0-9a-f]{64}', identity): wanted[table].add(identity)
    elif event_id: return {'status':'not_found','text':'互动记录编号无效。'}
    if refs.get('domain') == 'xhs_interactions':
        if refs.get('batch_id'): wanted['attention'].add(refs['batch_id'])
        wanted['interactions'].update(refs.get('interaction_ids') or [])
        wanted['replies'].update(refs.get('reply_ids') or [])
    sources, receipts = [], []
    image_interactions=set(wanted['interactions'])
    for record in _records(path, 'attention', wanted['attention']):
        if session_id and record.get('session_id') != session_id: continue
        if record.get('batch_id') not in wanted['attention']: continue
        sources.append(_payload(record))
        image_interactions.update(capture(_payload(record))['refs']['interaction_ids'])
        wanted['replies'].update(capture(_payload(record))['refs']['reply_ids'])
        # The exact materials AI received are in this immutable attention, so
        # don't duplicate them using a later interaction read.
        wanted['interactions'].difference_update(capture(_payload(record))['refs']['interaction_ids'])
    for record in _records(path, 'interactions', wanted['interactions']):
        if session_id and record.get('session_id') != session_id: continue
        if record.get('id') in wanted['interactions']:
            sources.append({'status':'read' if record.get('state') == 'read' else 'observed',
                            'interaction':record})
    for record in _records(path, 'replies', wanted['replies']):
        if session_id and record.get('session_id') != session_id: continue
        if record.get('id') in wanted['replies']: receipts.append(record)
        image_interactions.add(record['interaction_id'])
    if image_interactions:
        # Add later visual receipts separately; never rewrite a frozen attention.
        try:
            with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                rows=db.execute('SELECT record FROM image_reads WHERE interaction_id IN ('+
                    ','.join('?' for _ in image_interactions)+')',tuple(image_interactions)).fetchall()
            from .images import project
            later=[project(json.loads(row[0])) for row in rows
                   if not session_id or json.loads(row[0]).get('session_id')==session_id]
            if later:sources.append({'later_image_readings':later})
        except (sqlite3.Error,OSError,ValueError):pass
    if not sources and not receipts: return {'status':'not_found','text':'本地互动来源暂时不可用。'}
    # Explicit detail returns full authored text, without the search renderer's
    # 650-character excerpt limit. Current receipts are labelled separately.
    return {'status':'ok','text':_detail_text(sources,receipts)}
