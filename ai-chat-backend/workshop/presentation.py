"""Actual displayable entries, separate from execution/supporting files."""
import json
from pathlib import Path
from html.parser import HTMLParser


def kind(artifact):
    suffix = Path(artifact['name']).suffix.lower()
    if suffix in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
        return 'image'
    if suffix in ('.html', '.htm'):
        return 'html'
    if suffix in ('.txt', '.md'):
        return 'text'
    return 'supporting'


def entries(artifacts):
    return [row for row in artifacts if kind(row) != 'supporting']


def facts(service, job_id):
    with service.store.db() as db:
        artifacts = [dict(row) for row in db.execute(
            'SELECT * FROM artifacts WHERE job_id=? ORDER BY revision DESC,created_at DESC,id DESC', (job_id,))]
        receipts = [dict(row) for row in db.execute(
            "SELECT kind,body,created_at FROM receipts WHERE job_id=? AND kind IN ('K_received_visual','inspected_browser','accepted_work') ORDER BY seq DESC", (job_id,))]
    products = entries(artifacts)
    result = []
    for artifact in products[:8]:
        row = {key: artifact[key] for key in ('id', 'name', 'revision', 'sha256')}
        row.update(kind=kind(artifact), accepted_by_k=False, actual_observation=None, browser_observation=None)
        for receipt in receipts:
            body = json.loads(receipt['body'])
            if body.get('artifact_id') != artifact['id']:
                continue
            if receipt['kind'] == 'accepted_work':
                row['accepted_by_k'] = True
            else:
                observation = {'kind':receipt['kind'], 'at':receipt['created_at'],
                    'status':body.get('status'), 'checks_count':len(body.get('checks') or [])}
                if row['actual_observation'] is None:
                    row['actual_observation'] = observation
                if receipt['kind'] == 'inspected_browser' and row['browser_observation'] is None:
                    row['browser_observation'] = observation
        result.append(row)
    return {'source':'实际已交付的不可变文件与该文件的真实查看回执；需求和方案不是交付证明',
            'primary_entry':result[0] if result else None, 'entries':result,
            'supporting_files':[{'id':row['id'], 'name':row['name'], 'revision':row['revision']}
                                for row in artifacts if kind(row) == 'supporting'][:8]}


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def sharing_material(service, artifacts):
    rows = []
    for item in artifacts:
        artifact = service.artifact(item['id'])
        row = {key:artifact[key] for key in ('id', 'name', 'revision', 'sha256')}
        row.update(kind=kind(artifact), summary=item.get('summary') or artifact['summary'])
        if row['kind'] in ('html', 'text'):
            content = service.artifact_path(artifact).read_text(encoding='utf-8-sig')
            if row['kind'] == 'html':
                parser = VisibleText()
                parser.feed(content)
                content = '\n'.join(parser.parts)
            row['file_text_excerpt'] = content[:6000]
            row['excerpt_truncated'] = len(content) > 6000
            row['text_source'] = '实际文件的静态文字；不是浏览器运行或功能验证'
        rows.append(row)
    return rows
