"""Bounded, read-only history lookup. Recalled summaries are not primary facts."""
import asyncio
from pathlib import Path
from . import books


async def recall(messages):
    if books.ROOT != Path(__file__).resolve().parents[1]:
        return []
    from memory_v2.retrieval import search_records
    query = '\n'.join(m['text'] for m in messages[-8:])
    if not query:
        return []
    async def search():
        return [{'id': item['id'], 'text': item['content'],
                 'date': item['metadata']['source_date'],
                 'source': 'recalled_summary_not_primary_statement'}
                for item in await search_records(query, limit=8)]
    return await asyncio.wait_for(search(), timeout=25)
