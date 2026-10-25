"""Source-bound frequency hints, not a second habit or cognition authority."""
from dataclasses import asdict


def collect(signals, source, memory_paths):
    from habit_radar.frequency import resolve_signals
    rows, _ = resolve_signals(signals, source, memory_paths)
    return [asdict(row) for row in rows]


def select(rows, evidence):
    """Bind metadata to the full current statement and its unchanged content."""
    from hashlib import sha256
    digests = {row['id']: sha256(row['text'].strip().encode('utf8')).hexdigest()
               for row in evidence}
    texts = {row['id']: row['text'] for row in evidence}
    return [dict(row) for row in rows
            if row.get('source_message_id') in digests
            and row.get('source_content_digest') == digests[row['source_message_id']]
            and row.get('quote', '') in texts[row['source_message_id']]]


def model_rows(rows, n_of):
    """Keep opaque occurrence/discussion equality, using the existing short refs."""
    return [{**{key: row[key] for key in
                ('subject_id', 'pattern_key', 'occurrence_key', 'discussion_key',
                 'source_date', 'temporal_status')},
             **{key: row.get(key, '') for key in ('behavior_subject', 'behavior_type', 'binding_basis', 'quote')},
             'n': n_of[row['source_message_id']]}
            for row in rows if row['source_message_id'] in n_of]


GUIDE = '''frequency_evidence 是随原话绑定的记录身份：相同 occurrence_key 仅代表既有事件或事件链的同一次经历，discussion_key 表示提起它的场景（无场景时按来源日归并）。空 occurrence_key 表示次数未知。pattern_key 只是领域与事件类型的粗导航，不证明同一种行为；具体习惯、条件及变化由原话和指定旧认识共同判断。这些记录覆盖已提供材料，不是完整生活次数。'''
GUIDE += ' quote 区分同一消息中的不同线索。behavior_subject、behavior_type、temporal_status 是待复核的模型标注；legacy_event_anchor 是旧版粗导航。线程身份只证明既有连续关系，不能证明其中每种行为各发生一次。结合完整原话复核执行者、计划与实际，以及具体旧认识；材料不支持的认识保持未确认。'
