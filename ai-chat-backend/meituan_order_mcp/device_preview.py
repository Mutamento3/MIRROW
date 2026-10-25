"""Bounded Meituan browser probe. No cart, order, address or payment mutations."""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from android_device_gateway.gateway import AdbDeviceGateway, DeviceBusyError

PACKAGE = 'com.sankuai.meituan'
COMPONENT = 'com.meituan.android.pt.homepage.activity.MainActivity'
PREFIX = PACKAGE + ':id/'


def _bounds(node):
    values = re.findall(r'\d+', node.get('bounds', ''))
    return tuple(map(int, values)) if len(values) == 4 else None


def inspect_page(xml):
    """Return whitelisted state facts, never raw page/contact/location text."""
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, TypeError):
        return {'status': 'ui_unavailable', 'page': 'unknown'}, []
    nodes = [dict(node.attrib) for node in root.iter('node')]
    texts = {node.get('text', '').strip() for node in nodes}
    ids = {node.get('resource-id', '') for node in nodes}
    packages = {node.get('package') for node in nodes if node.get('package')}
    if packages and packages != {PACKAGE}:
        return {'status': 'foreign_window', 'page': 'unknown'}, nodes
    modal_labels = {'立即领取', '一键领取', '开心收下', '立即开通', '立即开通会员领券',
                    '立即购买', '确认授权', '允许', '仅在使用中允许', '不感兴趣', '关闭弹窗'}
    if (len(root.findall('node')) > 1 or any(text in texts for text in modal_labels) or
            any(re.search(r'(?:Dialog|PopupWindow)$', node.get('class', '')) for node in nodes)):
        return {'status': 'unrecognized_overlay', 'page': 'unknown'}, nodes
    if any(text in texts for text in ('无地址信息，请核对', '选择收货地址', '新增收货地址')):
        return {'status': 'address_required', 'page': 'delivery_address',
                'location_authority': 'explicit_browse_location_or_main_phone_required'}, nodes
    if any(text in texts for text in ('验证码登录', '手机号登录', '登录后继续', '登录体验完整功能', '输入验证码')):
        return {'status': 'login_required', 'page': 'login'}, nodes
    if any(text in texts for text in ('安全验证', '拖动滑块完成验证', '请完成验证')):
        return {'status': 'verification_required', 'page': 'verification'}, nodes
    if PREFIX+'waimai_channel_icon' in ids and PREFIX+'homepage_search_icon' in ids:
        return {'status': 'observed', 'page': 'delivery_home',
                'location_verified': False, 'nearby_availability_verified': False}, nodes
    if PREFIX+'search_layout_area' in ids and any(node.get('content-desc') == '外卖' for node in nodes):
        return {'status': 'observed', 'page': 'home', 'delivery_entry_observed': True}, nodes
    # Unknown screens are evidence of a screen, not evidence of a usable menu.
    return {'status': 'unrecognized_page', 'page': 'unknown'}, nodes


class MeituanDevicePreview:
    def __init__(self, *, gateway=None, sleeper=None):
        self.gateway = gateway or AdbDeviceGateway(command_timeout_seconds=18)
        self.sleep = sleeper or asyncio.sleep

    async def _observe(self):
        result, nodes = inspect_page(await self.gateway.ui_dump())
        for _ in range(2):
            if result['status'] not in {'unrecognized_page', 'ui_unavailable'}:
                break
            await self.sleep(1)
            result, nodes = inspect_page(await self.gateway.ui_dump())
        return result, nodes

    async def _tap_observed(self, node):
        bounds = _bounds(node)
        if not bounds or node.get('clickable') != 'true':
            return False
        x1, y1, x2, y2 = bounds
        if x2 <= x1 or y2 <= y1:
            return False
        return await self.gateway.tap((x1+x2)//2, (y1+y2)//2)

    async def inspect(self, *, open_delivery=True):
        facts = {'source': 'meituan_android_accessibility', 'mode': 'browse_probe',
                 'observed_at': datetime.now(timezone.utc).isoformat(),
                 'order_submitted': False, 'cart_modified': False,
                 'checkout_verified': False, 'final_payable': None,
                 'coupon_handling': 'verified_close_selectors_only', 'steps': []}
        try:
            lease = await self.gateway.lease_async('meituan_preview')
        except DeviceBusyError:
            return dict(facts, status='android_device_busy', page='unknown')
        try:
            if await self.gateway.get_state() != 'device':
                return dict(facts, status='device_unavailable', page='unknown')
            battery = await self.gateway.battery()
            if battery.get('level') is not None and battery['level'] < 20 and not battery.get('charging'):
                return dict(facts, status='low_battery', page='unknown')
            await self.gateway.wake()
            if not await self.gateway.launch(PACKAGE, component=COMPONENT):
                return dict(facts, status='launch_failed', page='unknown')
            await self.sleep(2)
            result, nodes = await self._observe()
            # Only the close control witnessed on the tested app is automated.
            # Receiving coupons, permissions and membership buttons are never substitutes.
            closes = [node for node in nodes if node.get('resource-id') ==
                      PREFIX+'locate_guide_bottom_layer_close_btn' and node.get('clickable') == 'true']
            if result.get('page') == 'home' and len(closes) == 1 and await self._tap_observed(closes[0]):
                facts['steps'].append('closed_location_promotion')
                await self.sleep(1)
                result, nodes = await self._observe()
            if open_delivery and result.get('page') == 'home':
                entries = [node for node in nodes if node.get('content-desc') == '外卖' and
                           node.get('clickable') == 'true' and _bounds(node) and
                           _bounds(node)[1] < 1000 and _bounds(node)[3]-_bounds(node)[1] < 400]
                if len(entries) != 1:
                    return dict(facts, status='delivery_entry_ambiguous', page='home')
                if not await self._tap_observed(entries[0]):
                    return dict(facts, status='navigation_failed', page='home')
                facts['steps'].append('opened_delivery')
                await self.sleep(2)
                result, nodes = await self._observe()
            return dict(facts, **result)
        finally:
            lease.release()
