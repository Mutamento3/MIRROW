"""Topic annotations and directories over authoritative book sections.

Classification is supplied by the existing semantic writer, not guessed from
keywords. The directory is a read view: no independent knowledge store, model
calls, or implicit migration of old Markdown.
"""
import hashlib
import re

TYPES = {'behavior', 'interest', 'belief', 'self_relation', 'knowledge_skill'}

CLASSIFICATION_CONTRACT = '''topics 是正文的分类，不是新的认识或证据。每项为 {"section":"正文二级栏目标题；无栏目时空字符串","types":["behavior|interest|belief|self_relation|knowledge_skill"],"areas":["general|writing|life_rhythm|music|gaming|social 或其他明确领域"],"context":"正文已有的适用情境"}。
types 可交叉：行为规律、兴趣审美、观念价值、自我与关系理解、事实知识能力；areas 是消费领域而不是书籍归属。分类由正文意义决定，同一主题可服务多个领域，只维护一份正文。
每个真实 section 只提交一项分类；无二级栏目时，整个正文对应 section=""，也只提交一项。多个类型或消费领域分别写在同一项的 types、areas 列表中。context 完整保留正文已有的适用条件和例外；不能用同一 section 的多个分类项代替不同情境，也不将不同情境并成无条件的认识。确实属于不同栏目时，沿正文真实栏目分别分类。
原件未明确的范围保持未知；分类不补写正文未表达的条件。历史条目没有分类仍需读取正文，不能按关键词猜分类或强制补齐五类。
behavior 主题可附 habit_dimensions:["food|sleep_rhythm|work|leisure|expression"]，表示该具体认识需要核对哪类原材料，可多选；这是材料路由，不是行为次数或长期成立证明。没有对应材料维度保持空，旧分类没有这个字段时不猜补。
一次情绪与观念可以同时有依据；明确观念不等待行为次数达标。兴趣可以并存、深化、跨领域延伸，只有材料支持才描述替代或因果。'''

CONTRACT = '''认识按主题组织：每个主题回答一个明确问题，相关条件、例外、发展和变化保留在一起。
现有相同主题优先完整修订；相关但不同的问题分开保存，事实与 AI 的理解保留各自性质。
''' + CLASSIFICATION_CONTRACT


def sections(body):
    """Structural Markdown parsing only; no semantic boundary guessing."""
    parts = re.split(r'^##\s+(.+?)\s*$', body.strip(), flags=re.M)
    return [('', parts[0].strip()), *[(parts[i].strip(), parts[i + 1].strip())
                                     for i in range(1, len(parts), 2)]]


def normalize(value, body):
    from .books import BookError
    if not isinstance(value, list):
        raise BookError('主题分类需要列表')
    available = {title for title, _ in sections(body)}
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise BookError('主题分类结构无效')
        section = item.get('section', '')
        if (not isinstance(section, str) or section not in available or section in seen):
            raise BookError('主题分类栏目不存在或重复')
        fields = {}
        for key in ('types', 'areas'):
            values = item.get(key, [])
            if (not isinstance(values, list) or any(not isinstance(v, str) or not v.strip()
                    or '\n' in v or '\r' in v for v in values)):
                raise BookError('主题类型与领域需要文本列表')
            fields[key] = list(dict.fromkeys(v.strip() for v in values))
        if set(fields['types']) - TYPES:
            raise BookError('主题认识类型无效')
        context = item.get('context', '')
        if not isinstance(context, str):
            raise BookError('主题适用范围需要文本')
        dimensions = item.get('habit_dimensions', [])
        if (not isinstance(dimensions, list) or any(not isinstance(v, str) or v not in
                {'food', 'sleep_rhythm', 'work', 'leisure', 'expression'} for v in dimensions)
                or (dimensions and 'behavior' not in fields['types'])):
            raise BookError('习惯材料维度需要行为主题及已知维度')
        result.append({'section': section, **fields,
                       **({'habit_dimensions': list(dict.fromkeys(dimensions))} if 'habit_dimensions' in item else {}),
                       **({'context': context.strip()} if 'context' in item else {})})
        seen.add(section)
    return result


def merge_annotations(previous, changes, body):
    by_section = {row['section']: row for row in normalize(previous, body)}
    by_section.update({row['section']: row for row in normalize(changes, body)})
    return normalize(list(by_section.values()), body)


def carry_annotations(previous, before, after):
    """Keep classification only for unchanged text; edits do not guess new scopes."""
    old, new = dict(sections(before)), dict(sections(after))
    return [row for row in normalize(previous, before)
            if row['section'] in new and old.get(row['section']) == new[row['section']]]


def project(entry):
    annotations = {row['section']: row for row in normalize(entry.get('topics', []), entry['body'])}
    result = []
    for section, text in sections(entry['body']):
        if not text:
            continue
        annotation = annotations.get(section, {})
        identity = f"{entry['domain']}:{entry['entry_id']}:{section}"
        result.append({key: entry.get(key) for key in
                       ('domain', 'entry_id', 'revision', 'name', 'subject_id', 'knower_id',
                        'kind', 'basis', 'scope', 'state')}
                      | {'topic_id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                         'section': section, 'body': text,
                         'types': annotation.get('types', []), 'areas': annotation.get('areas', []),
                         'context': annotation.get('context', ''),
                         'classification': 'classified' if annotation else 'unclassified'})
        result[-1]['habit_dimensions'] = annotation.get('habit_dimensions', [])
    return result


def directory(*, subject_id=None, area=None, knowledge_type=None):
    from . import books
    from .other_book import effective
    if subject_id is not None and subject_id not in books.subject_catalog():
        raise books.BookError('主题对象未登记')
    if knowledge_type is not None and knowledge_type not in TYPES:
        raise books.BookError('主题认识类型无效')
    entries = [entry for domain in ('self', 'other', 'world') for entry in books.catalog(domain)
               if entry.get('state', 'active') == 'active'
               and (domain != 'other' or effective(entry))
               and (subject_id is None or entry['subject_id'] == subject_id)]
    rows = [row for entry in entries for row in project(entry)]
    return {'topics': [row for row in rows if (area is None or area in row['areas'])
                      and (knowledge_type is None or knowledge_type in row['types'])],
            'unclassified_count': sum(row['classification'] == 'unclassified' for row in rows),
            'source': '现行认知书的主题读视图；未分类不等于没有相关认识'}
