"""Lossless selected social material for AI; no reads, writes or model calls.

Stores own the posts. This view selects a comment window and explicitly marks
its coverage, retaining full selected text, attribution and source clocks.
"""
from copy import deepcopy
from datetime import datetime
import json
import math
import os
from zoneinfo import ZoneInfo


VISIBLE_COMMENTS = 8
TIME_FACTS = (
    'published_at 是动态或评论的原始发布时间，edited_at 是来源记录的更新时间；'
    'reacted_at 是点赞时间，notified_at 是通知产生时间，read_at 是本节点取得页面的时间，彼此独立。'
    '日期使用完整年月日和时区偏移；时间未记录表示来源未提供可解析时间。'
    'comments_shown/omitted 表示本次评论窗口，未展示部分仍在来源动态；'
    'reply_context 只补充本页已有数据中的直接父评论，不代表完整回复链。'
)


def source_time(value):
    """Format epoch seconds in the configured local zone, never use read time."""
    try:
        if value is None or isinstance(value, bool):
            return '时间未记录'
        stamp = float(value)
        if not math.isfinite(stamp):
            return '时间未记录'
        return datetime.fromtimestamp(stamp, ZoneInfo(os.getenv('TIMEZONE', 'Asia/Shanghai'))).isoformat(timespec='seconds')
    except (TypeError, ValueError, OverflowError, OSError):
        return '时间未记录'


def project_notice(row):
    result = {key: row.get(key) for key in (
        'site_id', 'site_name', 'id', 'actor', 'actor_name', 'kind',
        'moment_id', 'comment_id', 'created_at') if key in row}
    result['notified_at'] = source_time(row.get('created_at'))
    for key in ('moment_content', 'comment_content'):
        if key in row:
            result[key] = row[key]
    return result


def _comment(row):
    return {**{key: row.get(key) for key in ('id', 'author', 'reply_to_id', 'created_at')},
            'content': row.get('content'), 'published_at': source_time(row.get('created_at')),
            'mention_actor_ids': list(row.get('mention_actor_ids') or [])}


def read_comment_ids(moment):
    """Only IDs whose text entered this selected view may be reply targets."""
    result = {str(row.get('id')) for row in moment['comments']}
    for row in moment['comments']:
        parent = (row.get('reply_context') or {}).get('comment')
        if parent:
            result.add(str(parent['id']))
    return result


def compact_moment(item, highlighted_ids=None, own_actor='k'):
    comments = list(item.get('comments') or [])
    highlighted_ids = highlighted_ids or set()
    priority = [row for row in comments if str(row.get('id') or '') in highlighted_ids]
    own = [row for row in comments if row.get('author') == own_actor][-2:]
    selected_ids = {str(row.get('id')) for row in [*priority, *own]}
    selected = [*priority, *[row for row in own if str(row.get('id')) not in highlighted_ids]]
    selected.extend(row for row in reversed(comments) if str(row.get('id')) not in selected_ids)
    selected = sorted(selected[:max(VISIBLE_COMMENTS, len(priority))],
                      key=lambda row: (row.get('created_at') or 0, str(row.get('id'))))
    selected_ids = {str(row.get('id')) for row in selected}
    by_id = {str(row.get('id')): row for row in comments}
    projected_comments, parents = [], []
    for row in selected:
        view = _comment(row)
        parent_id = str(row.get('reply_to_id') or '')
        if parent_id:
            parent = by_id.get(parent_id)
            if parent_id in selected_ids:
                view['reply_context'] = {'status': 'in_selected_comments'}
            elif parent:
                view['reply_context'] = {'status': 'outside_selected_comments', 'comment': _comment(parent)}
                parents.append(parent)
            else:
                view['reply_context'] = {'status': 'unavailable_in_source_snapshot'}
        projected_comments.append(view)

    reactions = list(item.get('reactions') or [])
    shown_reactions = reactions[-16:]
    forward = deepcopy(item.get('forward'))
    forward_source = (forward or {}).get('source') or {}
    if forward_source:
        forward_source['published_at'] = source_time(forward_source.get('created_at'))
    visible_actors = {item.get('author'), forward_source.get('author'),
                      *(row.get('author') for row in [*selected, *parents, *shown_reactions]),
                      *(item.get('mention_actor_ids') or []),
                      *(actor for row in [*selected, *parents] for actor in (row.get('mention_actor_ids') or []))}
    people = item.get('people') or {}
    mention_targets = [{key: person.get(key) for key in ('actor_id', 'name', 'nickname')}
                       for actor, person in people.items()
                       if actor and isinstance(person, dict) and actor in visible_actors]
    result = {
        'id': item.get('id'), 'author': item.get('author'),
        **({'source': deepcopy(item['source']), 'moment_ref': json.dumps([item['source']['site_id'], item.get('id')], ensure_ascii=False)} if item.get('source') else {}),
        'viewer_actor': item.get('viewer_actor') or own_actor,
        'content': item.get('content'), 'visibility': item.get('visibility'),
        **({'forward': forward} if forward else {}),
        'created_at': item.get('created_at'), 'published_at': source_time(item.get('created_at')),
        'mention_actor_ids': list(item.get('mention_actor_ids') or []),
        'comment_count': len(comments), 'comments_shown': len(selected),
        'comments_omitted': len(comments) - len(selected), 'comments': projected_comments,
        'reaction_count': len(reactions), 'liked_by': [row.get('author') for row in shown_reactions],
        'reactions': [{**{key: row.get(key) for key in ('author', 'reaction', 'created_at')},
                       'reacted_at': source_time(row.get('created_at'))} for row in shown_reactions],
        'reactions_shown': len(shown_reactions), 'reactions_omitted': len(reactions) - len(shown_reactions),
        'people': {actor: {key: person.get(key) for key in ('actor_id', 'nickname', 'remark', 'name')}
                   for actor, person in people.items() if actor in visible_actors},
        'mention_targets': mention_targets[:16],
    }
    if 'updated_at' in item:
        result.update(updated_at=item['updated_at'], edited_at=source_time(item['updated_at']))
    for key in ('revision', 'migrated', 'arrived_from_other_home', 'hosting_mode'):
        if key in item:
            result[key] = item[key]
    return result
