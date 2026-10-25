"""Shared topic/person recall lanes for social consumers, with no model gate."""
import asyncio


async def retrieve(query, bindings, known, search=None):
    from memory_v2.retrieval import search_records
    search = search or search_records
    anchors = list(dict.fromkeys(b['entity_id'] for b in bindings if b['entity_id'] in known))
    queries = [query] + [known[sid]['name'] + ' ' + ' '.join(known[sid].get('aliases', [])) + '\n' + query
                         for sid in anchors]
    lanes = await asyncio.gather(*(search(q, limit=3 if i == 0 else 2, max_record_chars=1200)
                                  for i, q in enumerate(queries)), return_exceptions=True)
    records, seen = [], set()
    for index in range(3):
        for lane in lanes:
            if isinstance(lane, BaseException) or index >= len(lane):
                continue
            record = lane[index]
            if record['id'] not in seen:
                seen.add(record['id'])
                records.append(record)
    return records[:6], sum(isinstance(lane, BaseException) for lane in lanes), queries
