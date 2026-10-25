"""Describe local wiring separately from native executor discovery."""
from __future__ import annotations

def describe(native=None):
    native = native or {}
    result = {'files_shell_git': '已接入：工单目录内制作与读回文件，以及 MIRROW 脱敏源码只读检索和身体能力目录。原生 shell/Git 尚未完成执行验收。', 'browser': '已接入：本地 HTML 实际预览、点击与截图；每次检查保留真实回执。', 'delivery': '已接入：复制真实作品文件、校验版本并返回摘要与下载入口。', 'desktop_sidebar_sync': '工单保存在 MIRROW；尚未接入桌面 Codex 的原生侧栏同步。'}
    for key, provider_key, flag_key in (('web_search', 'webSearch', None),):
        supported = native.get('provider', {}).get(provider_key)
        enabled = native.get('features', {}).get(flag_key) if flag_key else True
        if supported is False or enabled is False:
            text = '执行器报告未提供或未开启此功能。'
        elif supported is True and enabled is True:
            text = '执行器已确认支持并开启；具体工单的调用结果以真实回执为准。'
        else:
            text = '暂未取得执行器探测结果；点击刷新重新查询。'
        result[key] = text
    return {key: result[key] for key in ('files_shell_git', 'web_search', 'browser', 'delivery', 'desktop_sidebar_sync')}
