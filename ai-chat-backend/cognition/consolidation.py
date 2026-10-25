"""Source-bound, lossless historical regrouping with recoverable publication.

The semantic model chooses themes; the program carries whole old sections.
This is structural maintenance, not a new daily writer or cognition authority.
"""
import json
from datetime import datetime
import frontmatter
from . import books, topics, maintenance

CONTRACT = '''整理现行认知书的主题和存量重复，只做旧文分组，不新增或改写认识。
每份 books 有短序号 n、完整正文与归属。先按具体认识问题、主体、认识性质与适用范围判断哪些旧页确实可以归并；相关但不同的问题仍分开。self/other/world 不能跨书合并，归属、core、basis、scope、expires_at 不一致时保持原件。
旧段落由程序逐字搬移，blocks 只引用 {"n":1,"section":"原二级标题；整体印象为空字符串"}；每个非空旧栏目恰好选一次。仅当相同文字和适用范围相同才合为一个主题。整体印象可以保留 overview_blocks，具体主题放在 themes。每个主题回答一个具体问题，保留条件、例外和全部旧内容，不能按短句碎拆。近期栏目与其长期基线仍采用同名主题和 ·近期变化 配对；不改观察时间或生成新日期。
给主题分类 types/areas/context；行为主题可给 habit_dimensions（food/sleep_rhythm/work/leisure/expression），仅路由材料，不判长期成立。只有一个来源页的 group 可以只整理分类而不合并文件。
输出 {"groups":[{"target_n":1,"source_ns":[1,2],"overview_blocks":[{"n":1,"section":""}],"themes":[{"section":"具体主题","blocks":[{"n":2,"section":""}],"types":[],"areas":[],"context":"旧文已有适用范围","habit_dimensions":[]}]}]}。每份源书恰好属于一个 group，target_n 必须是其中的现行源书。无需改变的书同样保留原块，未分类不猜事实，未知归属不认领为新实体。'''
CONTRACT += '\n' + topics.CLASSIFICATION_CONTRACT + '\n只有 types 包含 behavior 时 habit_dimensions 才能非空。types 必须使用列出的五种值，每个数组元素只填一个实际值，不填竖线连接的字符串。'
REVIEW = '''独立核对旧页与程序组装的分组方案；只批准或拒绝，不改正文。
sources 是每组的全部原件；after 是逐字块组装的完整正文，分类不是新证据。逐项核对确为同主体、同认识性质与同具体问题，适用范围与时间层保留，相关但不同的认识未错误合并，标题和分类未添加旧文没有的判断，有限或当次认识没有升级为长期；长期与近期配对仍清楚。完整原文保留只证明没有丢字，不自动证明分组合理。
返回 {"approved_indices":[],"rejections":[{"index":0,"reason":"原因"}]}，覆盖每组一次。'''


def snapshot():
    from .book_materials import project_snapshot
    rows = project_snapshot({d: books.catalog(d) for d in ('self', 'other', 'world')})
    return [{'n': i + 1, **entry} for i, entry in enumerate(
        entry for entries in rows.values() for entry in entries if entry.get('state', 'active') == 'active')]


def build(rows, raw):
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict) or not isinstance(value.get('groups'), list):
        raise books.BookError('归并分组结构无效')
    indexed = {row['n']: row for row in rows}
    expected = {(row['n'], section): body for row in rows for section, body in topics.sections(row['body']) if body}
    used_pages, used_blocks, plans = set(), set(), []
    for group in value['groups']:
        ns = group.get('source_ns', [])
        if (not ns or any(type(n) is not int or n not in indexed or n in used_pages for n in ns)
                or len(ns) != len(set(ns)) or group.get('target_n') not in ns):
            raise books.BookError('归并来源重复或目标无效')
        sources = [indexed[n] for n in ns]
        target = indexed[group['target_n']]
        signature = lambda e: tuple(e.get(k) for k in ('domain', 'subject_id', 'knower_id', 'kind', 'core', 'scope', 'basis', 'expires_at'))
        if any(signature(source) != signature(target) for source in sources):
            raise books.BookError('不同主体、认识性质或范围保留各自原件')
        def collect(blocks):
            texts = []
            if not isinstance(blocks, list):
                raise books.BookError('旧文块需要列表')
            for block in blocks:
                key = (block.get('n'), block.get('section'))
                if key not in expected or key[0] not in ns or key in used_blocks:
                    raise books.BookError('旧文来源不存在或重复')
                used_blocks.add(key)
                texts.append(expected[key])
            return '\n\n'.join(dict.fromkeys(texts))
        overview = collect(group.get('overview_blocks', []))
        bodies, annotations = [overview] if overview else [], []
        for theme in group.get('themes', []):
            title = theme.get('section')
            if (not isinstance(title, str) or (title and not title.strip())
                    or '\n' in title or '\r' in title or (title == '' and bodies)):
                raise books.BookError('归并主题标题无效')
            text = collect(theme.get('blocks'))
            if not text:
                raise books.BookError('归并主题缺少原文')
            bodies.append(('## ' + title + '\n' if title else '') + text)
            annotations.append({k: v for k, v in theme.items() if k != 'blocks'})
        body = '\n\n'.join(bodies)
        normalized = topics.normalize(annotations, body)
        if len(normalized) != len(annotations):
            raise books.BookError('归并主题分类不完整')
        plans.append({'target': target, 'sources': sources, 'after': body, 'topics': normalized})
        used_pages.update(ns)
    if used_pages != set(indexed) or used_blocks != set(expected):
        raise books.BookError('归并未完整保留所选旧页和栏目')
    return plans


def decisions(raw, count):
    from .review_receipt import parse
    # Coverage is shared; regrouping does not impersonate profile points.
    _, approved, _ = parse(raw, count)
    return approved


def check_current(plans):
    for plan in plans:
        for source in plan['sources']:
            current = books._read(books.locate(source['domain'], source['name']), source['domain'])
            if current['entry_id'] != source['entry_id'] or current['revision'] != source['revision']:
                raise books.RevisionConflict('归并来源已更新，未发布旧方案')


def publish(plans, approved):
    """Explicit confirmed writer, not invoked by preview or book reads.

    Rollback is exact-CAS and snapshots precede every write. Interrupted
    transactions stay recorded; recovery never overwrites newer content.
    """
    selected = [plan for i, plan in enumerate(plans) if i in approved]
    rid = maintenance.digest({'consolidation': selected})
    root = books.ROOT / 'data/cognition_consolidations' / rid
    report_path = root / 'transaction.json'
    if report_path.exists():
        previous = json.loads(report_path.read_text('utf8'))
        if previous['status'] == 'completed':
            return previous
        if previous['status'] != 'restored':
            raise books.BookError('上次归并未完成，先核对并恢复事务')
        # An explicit, exact-CAS restore permits another attempt with the same
        # reviewed sources; retain the failed attempt's recovery record.
        check_current(selected)
        books.atomic_text(root / ('restored-' + datetime.now().strftime('%Y%m%d%H%M%S%f') + '.json'),
                          json.dumps(previous, ensure_ascii=False))
    with books.LOCK:
        check_current(selected)
        originals, written, changed = {}, {}, []
        for plan in selected:
            for source in plan['sources']:
                path = books.locate(source['domain'], source['name'])
                relative = path.relative_to(books.ROOT).as_posix()
                originals[relative] = path.read_text('utf8')
                if books.revision(originals[relative]) != source['revision']:
                    raise books.RevisionConflict('归并备份期间来源已变化')
                books.atomic_text(root / 'originals' / relative, originals[relative])
                if (root / 'originals' / relative).read_text('utf8') != path.read_text('utf8'):
                    raise books.RevisionConflict('归并备份校验失败')
        report = {'id': rid, 'status': 'applying', 'created_at': datetime.now().astimezone().isoformat(),
                  'before': {p: books.revision(raw) for p, raw in originals.items()}, 'after': {}, 'write_intents': {}}
        books.atomic_text(report_path, json.dumps(report, ensure_ascii=False))
        def intent(path, text):
            relative = path.relative_to(books.ROOT).as_posix()
            report['write_intents'].setdefault(relative, []).append(books.revision(text))
            books.atomic_text(report_path, json.dumps(report, ensure_ascii=False))
            written[relative] = text
        def write_owned(path, text):
            intent(path, text)
            books.atomic_text(path, text)
        try:
            if selected:
                for plan in selected:
                    target = plan['target']
                    origins = [{k: source[k] for k in ('domain', 'name', 'entry_id', 'revision')}
                               for source in plan['sources']]
                    prior_links = [link for source in plan['sources']
                        for link in books._read(books.locate(source['domain'], source['name']), source['domain'])['maintenance_links']
                        if isinstance(link, dict)]
                    evidence_ids = list(dict.fromkeys(mid for link in prior_links for mid in link.get('evidence_ids', [])
                        if isinstance(mid, str)))
                    source_dates = [link['source_date'] for link in prior_links if isinstance(link.get('source_date'), str) and link['source_date']]
                    entry = books.save(target['domain'], target['name'], {**target, 'body': plan['after'], 'topics': plan['topics']},
                        receipt='consolidation:' + rid, editor='人类伙伴确认的原文保真归并',
                        provenance={'consolidation_id': rid, 'merged_from': origins,
                            'evidence_ids': evidence_ids, 'source_date': max(source_dates) if source_dates else ''},
                        notify=False, before_write=intent)
                    path = books.locate(target['domain'], target['name'])
                    relative = path.relative_to(books.ROOT).as_posix()
                    written[relative] = path.read_text('utf8')
                    post = frontmatter.loads(written[relative])
                    historical = [link for source in plan['sources'] if source['entry_id'] != target['entry_id']
                                  for link in books._read(books.locate(source['domain'], source['name']), source['domain'])['maintenance_links']]
                    post['cognition_updates'] = [*historical, *post.get('cognition_updates', [])]
                    post['aliases'] = list(dict.fromkeys([*post.get('aliases', []),
                        *[source['name'] for source in plan['sources'] if source['entry_id'] != target['entry_id']]]))
                    write_owned(path, frontmatter.dumps(post) + '\n')
                    written[relative] = path.read_text('utf8')
                    for source in plan['sources']:
                        if source['entry_id'] == target['entry_id']:
                            continue
                        path = books.locate(source['domain'], source['name'])
                        relative = path.relative_to(books.ROOT).as_posix()
                        if books.revision(path.read_text('utf8')) != source['revision']:
                            raise books.RevisionConflict('旧来源在归并提交期间已更新')
                        post = frontmatter.loads(originals[relative])
                        post['cognition'] = {**post.get('cognition', {}), 'state': 'merged',
                            'redirect_to': {'domain': target['domain'], 'entry_id': target['entry_id']},
                            'consolidation_id': rid, 'merged_source_revision': source['revision']}
                        write_owned(path, frontmatter.dumps(post) + '\n')
                        written[relative] = path.read_text('utf8')
                    changed.append(target['domain'])
            report.update(status='completed', after={p: books.revision(raw) for p, raw in written.items()})
            books.atomic_text(report_path, json.dumps(report, ensure_ascii=False))
        except Exception:
            import traceback
            books.atomic_text(root / 'failure-local.txt', traceback.format_exc())
            for relative, raw in written.items():
                path = books.ROOT / relative
                if books.revision(path.read_text('utf8')) in report['write_intents'].get(relative, []):
                    books.atomic_text(path, originals[relative])
            report.update(status='failed', after={p: books.revision(raw) for p, raw in written.items()})
            books.atomic_text(report_path, json.dumps(report, ensure_ascii=False))
            raise
        for domain in set(changed):
            books.reload_domain(domain)
        from .brief import invalidate
        invalidate()
        return report


def restore(rid):
    """Explicit rollback accepts only saved originals or our own write intents."""
    maintenance.record_path(rid)
    root = books.ROOT / 'data/cognition_consolidations' / rid
    report_path = root / 'transaction.json'
    with books.LOCK:
        report = json.loads(report_path.read_text('utf8'))
        paths = []
        for relative, before in report['before'].items():
            path = books.ROOT / relative
            if not path.resolve().is_relative_to(books.ROOT.resolve()) or path.suffix != '.md':
                raise books.BookError('归并恢复路径无效')
            raw = (root / 'originals' / relative).read_text('utf8')
            allowed = [before, *report.get('write_intents', {}).get(relative, [])]
            if books.revision(raw) != before or books.revision(path.read_text('utf8')) not in allowed:
                raise books.RevisionConflict('归并后已有新修改，保留现状等待核对')
            domain = next((domain for domain in ('self', 'other', 'world')
                if path.resolve().is_relative_to(books.folder(domain).resolve())), None)
            if domain is None:
                raise books.BookError('恢复目标不在原认知书中')
            paths.append((domain, path, raw))
        for _, path, raw in paths:
            books.atomic_text(path, raw)
        report['status'] = 'restored'
        books.atomic_text(report_path, json.dumps(report, ensure_ascii=False))
        for domain in {domain for domain, _, _ in paths}:
            books.reload_domain(domain)
        from .brief import invalidate
        invalidate()
        return report


def resolve(domain, entry_id):
    """Follow explicit old-entry lineage, never name similarity."""
    seen = set()
    with books.LOCK:
        while (domain, entry_id) not in seen:
            seen.add((domain, entry_id))
            entry = next((row for row in books.catalog(domain, include_merged=True) if row['entry_id'] == entry_id), None)
            if not entry:
                raise books.BookError('归并目标已不可用')
            if entry['state'] != 'merged':
                return entry
            target = entry.get('redirect_to')
            if not isinstance(target, dict) or target.get('domain') != domain:
                raise books.BookError('归并指向无效')
            entry_id = target.get('entry_id')
    raise books.BookError('归并指向存在环路')
