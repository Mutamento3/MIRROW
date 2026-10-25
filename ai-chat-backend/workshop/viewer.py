"""Browser host for a public artifact, retaining the workshop preview sandbox."""
import html
import secrets


def page(artifact, path, title):
    from .delivery_policy import IMAGES
    from .runtime import supports_mood
    from .preview import SANDBOX
    nonce = secrets.token_hex(16)
    root = '/api/workshop/artifacts/'+artifact['id']
    if path.suffix.lower() in ('.html','.htm'):
        content = f'<iframe id="work" title="作品预览" sandbox="{SANDBOX}" src="{root}/preview"></iframe>'
    elif path.suffix.lower() in IMAGES:
        content = f'<img alt="{html.escape(title,quote=True)}" src="{root}/preview">'
    else:
        content = '<p class="file-note">这是一份可保存的作品文件，请通过上方入口下载。</p>'
    script = ''
    if supports_mood(path):
        script = f'''<script nonce="{nonce}">
const frame=document.getElementById('work');let serial=0;
async function sendMood(){{
 if(document.hidden)return;const current=++serial;const controller=new AbortController();
 const timer=setTimeout(()=>controller.abort(),8000);
 let mood={{status:'unavailable',family:null,checked_at:new Date().toISOString()}};
 try{{const response=await fetch('/api/workshop/runtime/{artifact['id']}/mood',{{cache:'no-store',signal:controller.signal}});if(response.ok)mood=await response.json();}}catch{{}}finally{{clearTimeout(timer);}}
 if(current===serial)frame.contentWindow?.postMessage({{type:'mirrow-workshop-mood',version:1,artifact_id:'{artifact['id']}',mood}},'*');
}}
frame.addEventListener('load',sendMood);setInterval(sendMood,15000);document.addEventListener('visibilitychange',()=>{{if(!document.hidden)sendMood();}});sendMood();
</script>'''
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{html.escape(title)} · 小机工作坊</title>
<style nonce="{nonce}">*{{box-sizing:border-box}}body{{margin:0;background:#f4efe3;color:#474838;font:14px system-ui;display:flex;flex-direction:column;height:100dvh}}header{{display:flex;justify-content:space-between;gap:16px;align-items:center;padding:calc(12px + env(safe-area-inset-top)) 18px 12px;border-bottom:1px solid #d9cfb9}}header small{{display:block;color:#827a62;font-size:10px;letter-spacing:.1em}}h1{{font-size:15px;font-weight:500;margin:4px 0;overflow-wrap:anywhere}}a{{color:inherit;white-space:nowrap}}main{{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding-bottom:env(safe-area-inset-bottom)}}iframe{{width:100%;height:100%;border:0}}img{{max-width:100%;max-height:100%;object-fit:contain}}.file-note{{padding:24px}}</style></head>
<body><header><div><small>来自 AI 的小机工作坊 · v{artifact['revision']}</small><h1>{html.escape(title)}</h1></div><a href="{root}/download">保存作品 ↓</a></header><main>{content}</main>{script}</body></html>'''
    headers = {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
               'Content-Security-Policy':f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; img-src 'self'; frame-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"}
    return document, headers
