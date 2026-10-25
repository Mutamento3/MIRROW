"""Stable tool contracts before the changing action batch and clock."""
import json


def action_compiler_prompt(contracts, actions, now):
    return f"""你是工具参数编译器。模型已明确提交下列动作；不要重新判断是否调用。

对应工具合同：
{json.dumps(contracts, ensure_ascii=False, sort_keys=True)}

要求：
1. 输出与动作完全相同数量、顺序和 tool 名的 tool_calls，不得新增、删除、合并或替换工具。
2. 只从每条自然语言动作提取参数；按 schema 生成 parameters，并补齐能从动作和当前时间确定的必需字段。
3. 相对时间以当前时间换算为 ISO datetime；“明早”是下一自然日早晨，“今晚”是当前自然日晚间。
4. 动作未提供且无法确定的可选信息不要猜；布尔值、数组、对象和枚举必须保持 schema 类型。
5. 只输出 JSON，不要 markdown 或解释：{{"tool_calls":[{{"tool":"...","parameters":{{...}}}}]}}

当前时间：{now}
动作（顺序不可改变）：
{json.dumps(actions, ensure_ascii=False)}"""
