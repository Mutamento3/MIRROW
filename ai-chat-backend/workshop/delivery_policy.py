"""Classify executable behavior from real files; no model or prose inference."""
import base64
import io
from html.parser import HTMLParser

IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.interactive = False
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'select', 'textarea', 'audio', 'video'}:
            self.interactive = True
        if any(key.startswith('on') for key in attrs) or attrs.get('name') == 'mirrow-workshop-capabilities':
            self.interactive = True
        if (attrs.get('href') or '').strip().lower().startswith('javascript:'):
            self.interactive = True
        if tag == 'img':
            self.images.append(attrs.get('src') or '')

    handle_startendtag = handle_starttag


def classify(path):
    suffix = path.suffix.lower()
    if suffix in IMAGES:
        return 'image'
    if suffix in {'.txt', '.md'}:
        return 'text'
    if suffix in {'.html', '.htm'}:
        parser = CardParser()
        try:
            parser.feed(path.read_text(encoding='utf-8'))
        except UnicodeError:
            return 'static_card'  # The local readability check reports the failure.
        return 'standard' if parser.interactive else 'static_card'
    return 'standard'


def basic_checks(path, kind):
    from PIL import Image
    checks = []
    def verify_image(data):
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    try:
        if kind == 'image':
            verify_image(path.read_bytes())
            checks.append('图片文件可以解码打开')
        else:
            text = path.read_text(encoding='utf-8')
            if not text.strip():
                raise ValueError('empty')
            checks.append('正文文件非空且可以读取')
            if kind == 'static_card':
                parser = CardParser()
                parser.feed(text)
                for src in parser.images:
                    if not src.startswith('data:image/') or ';base64,' not in src:
                        return checks, '卡片含未内嵌的图片，作品预览可能缺图，请制作方修正。'
                    verify_image(base64.b64decode(src.split(';base64,', 1)[1], validate=True))
                checks.append(f'静态卡片的 {len(parser.images)} 张内嵌图片可解码')
    except (ValueError, OSError, SyntaxError):
        return checks, '交付文件或卡片内嵌图片无法完整读取，请制作方修正。'
    return checks, ''


def group_status(reports):
    reports = list(reports)
    ready = bool(reports) and all(row and row.get('status') in ('completed', 'skipped') for row in reports)
    light = ready and all(row['status'] == 'skipped' for row in reports)
    return {'review_ready': ready, 'lightweight_ready': light}


def job_status(store, job):
    from .progress import current_artifacts
    from .quality import latest
    if job['state'] not in ('awaiting_review', 'accepted'):
        return {'review_ready': False, 'lightweight_ready': False}
    reports = [latest(store, artifact['id']) for artifact in current_artifacts(store, job)]
    return {**group_status(reports), 'review_progress':review_progress(reports)}


def review_progress(reports):
    """Generic lifecycle facts only: safe even for a private work order."""
    reports = list(reports)
    statuses = {row.get('status') for row in reports if row}
    if statuses & {'failed', 'interrupted', 'obsolete'}:
        return {'phase':'blocked', 'label':'质检未完成 · 等 AI 处理',
                'waiting_for':'k', 'pending_k_action':'review_or_rework'}
    if 'budget_paused' in statuses:
        return {'phase':'resource_paused', 'label':'质检预算暂停',
                'waiting_for':'resources_or_resume', 'pending_k_action':None}
    ready = group_status(reports)
    if ready['review_ready']:
        light = ready['lightweight_ready']
        return {'phase':'ready', 'label':'已交付 · 可欣赏' if light else '质检已返回 · 等 AI 验收',
                'waiting_for':None if light else 'k', 'pending_k_action':None if light else 'accept'}
    return {'phase':'running' if 'running' in statuses else 'queued',
            'label':'Luna 正在质检' if 'running' in statuses else 'Luna 质检排队',
            'waiting_for':'codex', 'pending_k_action':None}


def skip_review(service, artifact, previous):
    """Append a local check, retaining any old Luna report verbatim."""
    import uuid
    from .quality import latest
    from .store import now
    if previous and (previous['status'] == 'running' or previous['body'].get('manual_review')):
        return None
    if previous and previous['body'].get('review_mode') == 'light':
        return previous
    try:
        path = service.artifact_path(artifact)
        kind = classify(path)
        if kind == 'standard':
            return None
        checks, error = basic_checks(path, kind)
    except (ValueError,OSError):
        kind,checks,error='unavailable',[],'交付文件缺失或已改变，请制作方重新交付。'
    body = {'review_mode':'light', 'delivery_kind':kind, 'basic_checks':checks,
            'execution_summary':error or '文件基础检查完成，这类静态作品免 Luna 质检，可以直接欣赏。',
            'coverage_note':'本地文件检查不代表审美认可、完整需求验收或 AI 已验收。',
            'previous_review_id':previous['id'] if previous else None}
    import json
    status = 'failed' if error else 'skipped'
    with service.store.db() as db:
        db.execute('INSERT INTO quality_reviews(id,artifact_id,job_id,revision,model,status,token_cap,usage_known,body,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex,artifact['id'],artifact['job_id'],artifact['revision'],'',status,0,1,json.dumps(body,ensure_ascii=False),error,now(),now()))
        if previous and previous['status'] == 'queued':
            db.execute("UPDATE quality_reviews SET status='obsolete',reason='改走静态作品基础检查',updated_at=? WHERE id=?", (now(),previous['id']))
        if previous:
            # Preserve cumulative review usage; the local check adds zero tokens.
            db.execute('UPDATE quality_reviews SET tokens=?,usage_known=? WHERE id=(SELECT id FROM quality_reviews WHERE artifact_id=? ORDER BY seq DESC LIMIT 1)',
                       (previous['tokens'],previous['usage_known'],artifact['id']))
        service.store._event(db,artifact['job_id'],'quality_skipped' if not error else 'quality_report',
                            {'artifact_id':artifact['id'],'revision':artifact['revision'],'source':'local_file_check',
                             'status':status,'summary':body['execution_summary']})
    return latest(service.store,artifact['id'])
