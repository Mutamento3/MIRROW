"""Explicit classification-only review; book prose and receipts stay intact."""
import json

from . import books
from .prompt_request import CognitionPrompt
from .topics import CLASSIFICATION_CONTRACT, normalize


async def review(entry, annotations, llm, *, source_date, run_id):
    """Return a revision-bound metadata proposal, not a book write.

Normal daily writing uses its original model call; this extra call is only
for an explicitly selected isolated classification, never every entry.
"""
    raw = await llm(CognitionPrompt(
        '本次只复核已完成认识的主题分类。完整正文是本次范围的权威，'
        '旧候选分类是待复核数据。保留正文已有的条件和例外，分类只描述正文；'
        '信息不足时该栏目可保持未分类。返回 JSON 对象 {"topics": [...]}，只提供分类。\n'
        + CLASSIFICATION_CONTRACT,
        {'entry': {key: entry[key] for key in ('entry_id', 'revision', 'body')},
         'candidate_annotations': annotations},
        stage='topic_annotation_review', source_date=source_date, run_id=run_id))
    if isinstance(raw, dict):
        raw = raw.get('content', '')
    raw = raw.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    result = json.loads(raw)
    if not isinstance(result, dict) or set(result) != {'topics'}:
        raise books.BookError('分类复核返回结构无效')
    topics = normalize(result['topics'], entry['body'])
    return {'entry_id': entry['entry_id'], 'revision': entry['revision'], 'topics': topics}
