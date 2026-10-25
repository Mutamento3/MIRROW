"""History projection for AI's explicit band actions; never infer that a note was read."""
import json


def action_fact(call):
    if not isinstance(call, dict) or call.get('tool') != 'band_touch':
        return ''
    params = call.get('parameters')
    params = params if isinstance(params, dict) else {}
    note = params.get('note')
    action = 'note' if isinstance(note, str) and note.strip() else params.get('action')
    if action not in ('note', 'vibrate'):
        return 'AI曾请求手环操作；参数不完整，未确认执行。'
    description = '向人类伙伴的手环发送纸条' if action == 'note' else '让人类伙伴的手环震动'
    if call.get('success') is True:
        status = '蓝牙写入已确认，不代表人类伙伴已看到或感受到'
    elif call.get('status') in ('rejected', 'skipped', 'blocked'):
        status = '请求未执行'
    else:
        status = '失败或结果未确认'
    fact = f'AI曾请求{description}；{status}。'
    note = params.get('note')
    if action == 'note' and isinstance(note, str):
        fact += '当时提交的纸条正文（历史内容）：' + json.dumps(note[:80], ensure_ascii=False) + '。'
    return fact
