"""Bounded offline browser preview; real screenshots and interaction receipts."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from .preview import HEADERS, SANDBOX


class BrowserUnavailable(RuntimeError):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


async def launch_browser(pw):
    from playwright.async_api import Error
    for attempt in range(2):
        try:
            return await pw.chromium.launch(channel='msedge',headless=True,timeout=15000)
        except Error as exc:
            # Edge can exit cleanly during startup (e.g. while updating). Nothing
            # in the work has run yet, so this one retry cannot replay interaction.
            if attempt == 0 and type(exc).__name__ == 'TargetClosedError':
                continue
            raise BrowserUnavailable('browser_launch_failed') from exc


@asynccontextmanager
async def browser_session(pw):
    from playwright.async_api import Error
    browser = await launch_browser(pw)
    try:
        yield browser
    except Error as exc:
        # After launch, never replay a possibly performed click or form submit.
        raise BrowserUnavailable('browser_session_failed') from exc
    finally:
        try:
            await browser.close()
        except Error:
            pass  # Closing an already closed process does not erase real results.

SUPPORTED_KEYS = ('Enter', 'Tab', 'Escape', 'Space')
ACTION_SCHEMA = {'type':'array', 'maxItems':16, 'items':{'type':'object', 'properties':{
    'action':{'type':'string','enum':['fill','click','press','expect_text','mood']},
    'selector':{'type':'string','maxLength':200}, 'value':{'type':'string','maxLength':1000}},
    'required':['action','selector'], 'additionalProperties':False}}


def validate_actions(actions):
    if actions is None:
        return []
    if not isinstance(actions,list) or len(actions)>16:
        raise ValueError('bounded_browser_actions_required')
    for item in actions:
        if not isinstance(item,dict) or set(item)-{'action','selector','value'} or item.get('action') not in ('fill','click','press','expect_text','mood'):
            raise ValueError('invalid_browser_action')
        if not isinstance(item.get('selector'),str) or not 1<=len(item['selector'])<=200:
            raise ValueError('bounded_selector_required')
        if (item['action']!='click' or 'value' in item) and (not isinstance(item.get('value'),str) or len(item['value'])>1000):
            raise ValueError('bounded_action_value_required')
        if item['action']=='press' and item['value'] not in SUPPORTED_KEYS:
            raise ValueError('supported_key_required')
        if item['action']=='mood':
            from .runtime import FAMILIES
            if item['selector'] != 'body' or item['value'] not in FAMILIES | {'unavailable'}:
                raise ValueError('bounded_mood_fixture_required')
    return actions


async def inspect_html(path, screenshot, selector='', *, actions=None, viewport=None):
    from playwright.async_api import async_playwright, expect
    path = Path(path).resolve()
    actions = validate_actions(actions)
    viewport = viewport or {'width':390,'height':844}
    if set(viewport)!={'width','height'} or any(type(v) is not int for v in viewport.values()) or not 320<=viewport['width']<=1600 or not 320<=viewport['height']<=1200:
        raise ValueError('bounded_viewport_required')
    errors = []
    console_errors = []
    checks = []
    async with async_playwright() as pw:
        async with browser_session(pw) as browser:
            page = await browser.new_page(viewport=viewport)
            page.on('pageerror', lambda error: errors.append(str(error)[:300]))
            page.on('console',lambda msg:console_errors.append(msg.text[:300]) if msg.type=='error' else None)
            origin = 'http://workshop-preview.test/'
            async def route(req):
                if req.request.url == origin:
                    await req.fulfill(content_type='text/html',body=f'<style>body{{margin:0}}iframe{{width:100%;height:100vh;border:0}}</style><iframe sandbox="{SANDBOX}" src="{origin}artifact.html"></iframe>')
                elif req.request.url == origin+'artifact.html':
                    await req.fulfill(content_type='text/html; charset=utf-8',body=path.read_text(encoding='utf8'),headers=HEADERS)
                else:
                    await req.abort()
            await page.route('**/*', route)
            await page.goto(origin, wait_until='load', timeout=20000)
            frame = page.frame_locator('iframe')
            if selector:
                await frame.locator(selector).first.click(timeout=5000)
            for item in actions:
                locator = frame.locator(item['selector']).first
                try:
                    if item['action']=='mood':
                        from .store import now
                        await page.evaluate("message => document.querySelector('iframe').contentWindow.postMessage(message, '*')", {
                            'type':'mirrow-workshop-mood','version':1,'artifact_id':'quality-fixture',
                            'mood':{'status':'unavailable' if item['value']=='unavailable' else 'available',
                                'family':None if item['value']=='unavailable' else item['value'], 'checked_at':now()}})
                    elif item['action']=='fill':
                        await locator.fill(item['value'],timeout=5000)
                    elif item['action']=='click':
                        await locator.click(timeout=5000)
                    elif item['action']=='press':
                        await locator.press(item['value'],timeout=5000)
                    else:
                        await expect(locator).to_contain_text(item['value'],timeout=3000)
                    checks.append({**item,'status':'passed' if item['action']=='expect_text' else 'performed'})
                except Exception as exc:
                    checks.append({**item,'status':'failed','error':type(exc).__name__})
                    break
            await page.screenshot(path=str(screenshot), full_page=False)
            return {'text': (await frame.locator('body').inner_text())[:12000], 'page_errors': errors,
                    'controls':await frame.locator('input,textarea,button,select').evaluate_all("nodes => nodes.slice(0,30).map(node => ({tag:node.tagName.toLowerCase(),id:node.id,type:node.getAttribute('type'),label:node.getAttribute('aria-label')||node.getAttribute('placeholder')||node.innerText?.slice(0,120)||''}))"),
                    'console_errors':console_errors, 'checks':checks, 'viewport':viewport,
                    'status':'failed' if errors or console_errors or any(row['status']=='failed' for row in checks) else
                        'passed' if any(row['action']=='expect_text' and row['status']=='passed' for row in checks) else
                        'interaction_only' if actions or selector else 'preview_only',
                    'interaction': {'action': 'click', 'selector': selector, 'status': 'performed'} if selector else None,
                    'screenshot': str(screenshot), 'browser': 'Chromium/msedge', 'source': '真实离线页面；同工作坊预览 CSP 与 iframe 沙箱'}
