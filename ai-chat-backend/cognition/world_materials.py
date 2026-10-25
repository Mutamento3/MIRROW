"""Wire-only short references; canonical records retain real IDs and full sources."""
from copy import deepcopy

REFERENCE_CONTRACT = (
    '\n材料引用的 n 对应 messages 的连续短序号；source_text_n 表示该引用的完整原文'
    '存放在对应 messages 条目的 text 中，请读取全文及其来源、时间和 source_complete，'
    '再核对 quote 与候选。真实消息 ID 由后端映射，不需要模型输出。'
)


def numbered_references(records, n_of, source_texts=None):
    result = deepcopy(records)
    for record in result:
        for ref in record.get('evidence', []):
            mid = ref.pop('message_id')
            if mid not in n_of:
                raise ValueError('world_material_reference_missing')
            ref['n'] = n_of[mid]
            # Store each full source once. Only replace an exact copy, so a
            # historical excerpt or differing source can never disappear.
            if (source_texts is not None and 'source_text' in ref
                    and ref['source_text'] == source_texts[mid]):
                ref.pop('source_text')
                ref['source_text_n'] = n_of[mid]
    return result
