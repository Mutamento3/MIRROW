"""Verify real WorkshopPage on localhost, including mobile and model sprites."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def check(output):
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto('http://127.0.0.1:5175/workshop-preview.html')
            await page.get_by_role('heading', name='小机手工作坊', exact=True).wait_for()
            await page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)")
            assert not await page.locator('dialog[open]').count()
            await page.screenshot(path=str(output/'工作坊桌面预览.png'), full_page=True)
            await page.get_by_role('button', name='工作坊设置', exact=True).click()
            await page.get_by_role('dialog', name='工作坊设置', exact=True).wait_for()
            await page.get_by_label('工具手模型').select_option('gpt-6.1-sol')
            assert (await page.locator('.workshop-worker').get_attribute('src')).endswith('/sol.gif')
            assert await page.locator('.workshop-sign span').inner_text() == '6.1 Sol'
            await page.get_by_label('工具手模型').select_option('gpt-6-astra')
            assert (await page.locator('.workshop-worker').get_attribute('src')).endswith('/astra.gif')
            assert await page.locator('.workshop-sign span').inner_text() == '6.0 Astra'
            await page.get_by_label('每张工单的 token 预算').fill('120000')
            await page.get_by_role('button', name='保存工作坊设置').click()
            await page.wait_for_timeout(500)
            assert not await page.locator('[role=alert]').count()
            await page.screenshot(path=str(output/'工作坊设置预览.png'), full_page=True)
            await page.keyboard.press('Escape')
            assert not await page.locator('dialog[open]').count()
            # Native dialog restores keyboard focus to the scene's trigger.
            await page.wait_for_function("document.activeElement?.getAttribute('aria-label') === '工作坊设置'")
            await page.get_by_role('button', name='工单与房间', exact=True).click()
            await page.get_by_role('button', name='隔离验收 · 星星小作品', exact=False).click()
            await page.locator('dialog').get_by_role('button', name='作品柜', exact=True).click()
            await page.get_by_role('button', name='打开作品', exact=True).click()
            frame = page.frame_locator('iframe[title="index.html"]')
            await frame.locator('output').wait_for()
            await frame.get_by_role('button', name='点亮星星，计数加一').click()
            assert await frame.locator('output').inner_text() == '1'
            await page.screenshot(path=str(output/'场景作品柜验收.png'), full_page=True)
            # Embedded works have their own keyboard context; use the host's close icon.
            await page.get_by_role('button', name='回到工作坊场景', exact=True).click()
            await page.locator('dialog').wait_for(state='hidden')
            await page.set_viewport_size({'width': 390, 'height': 844})
            await page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)")
            await page.screenshot(path=str(output/'工作坊手机预览.png'), full_page=True)
            await page.get_by_role('button', name='工作坊设置', exact=True).click()
            await page.get_by_label('工具手模型').wait_for()
            await page.screenshot(path=str(output/'工作坊手机设置.png'), full_page=True)
            await page.keyboard.press('Escape')
            await page.locator('dialog').wait_for(state='hidden')
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert not errors, errors
            print(json.dumps({'desktop': 'passed', 'mobile': 'passed', 'scene_hotspots': 'passed', 'dialog_escape_focus': 'passed', 'real_artifact_click':'0->1', 'settings_save': 'passed', 'model_sprite_switch': 'passed', 'page_errors': errors}, ensure_ascii=False))
        finally:
            await browser.close()


if __name__ == '__main__':
    import sys
    asyncio.run(check(Path(sys.argv[1])))
