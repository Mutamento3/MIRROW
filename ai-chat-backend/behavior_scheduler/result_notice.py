"""Safe system notice when model result finalization fails; never quote payloads."""


def result_notice(tool_calls):
    records = [item for item in (tool_calls or [])
               if isinstance(item, dict) and item.get('delivery', 'model') != 'ui_only']
    if not records:
        return '【系统提示】本轮回复整理未完成，没有可确认的工具结果。'
    succeeded = sum(item.get('success') is True for item in records)
    rejected = sum(item.get('status') in ('rejected', 'skipped', 'blocked') for item in records)
    remaining = len(records) - succeeded - rejected
    parts = []
    if succeeded:
        parts.append(f'{succeeded} 项返回成功回执')
    if rejected:
        parts.append(f'{rejected} 项未执行')
    if remaining:
        parts.append(f'{remaining} 项失败或结果未确认')
    return '【系统提示】本轮回复整理未完成；' + '，'.join(parts) + '。详情请展开工具记录。'
