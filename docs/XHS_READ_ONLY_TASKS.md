# 小红书只读任务接入合同

默认 `local_app` 没有注册手机控制工具。这里是供独立宿主显式接入的组件，不是安装后自动浏览，也不包含私人生产环境的任务调度器。

## 两个入口

- `BrowseXiaohongshuHandler.fetch_one_post`：既有单篇漫想/公共卡片接口，保持兼容。
- `fetch_task_post(task=..., cursor=..., exclude_ids=...)`：新结构化只读端口。可以先由 `compile_task(instruction, compiler=...)` 将已经选定的行动编译成任务，也可以直接传经过 `validate_task` 检查的字典。

支持 recommend、search、profile。continue 是给宿主的恢复请求，组件单独收到时返回 `resume_requires_runtime`，不会猜测之前账号或自行重放动作。

```python
task = {
    'operation': 'profile',
    'query': '示例账号',
    'scope': 'count',
    'count': 3,
    'goal_count': 8,
    'task_id': 'host-owned-task-id',
}
material = await handler.fetch_task_post(task=task, cursor=saved_cursor)
```

一次调用取得一个真实笔记材料或明确状态。count（1–5）是宿主每批调度预算，不代表该调用自动抓取多篇；goal_count（1–500）是主页任务总目标。scope=all 仅供主页/恢复请求，表示遍历到有证据的页面末尾，不保证平台上所有历史内容都可见。

## 宿主负责

1. 显式配置自己的 ADB 设备、已有登录、视觉调用回调及所需模型。不得把个人设备地址、序列号、凭据或截图打包提交到仓库。
2. 保存任务身份与原始目标；同一任务串行调用，尊重失败、暂停及页面风险状态。组件只处理自己当前持有的设备租约。
3. 仅在 success 材料真实保存后，提交它的 cursor_after 与去重身份；失败不推进。恢复时读出原任务，再调用原 profile 任务及最后成功游标。`latest_cursor` 可从宿主成功节点回执投影。
4. 将 `material_for_k` 返回的来源材料交给最终模型自行理解；视觉摘要不是伴侣的主观感想。不把封面当已看完视频，不把局部文字当全文。

## 真实性边界

主页定位依据实际可见的账号证据；同名、无可靠身份、列表停滞等情况不会冒充已经找到正确账号或读到末尾。文章与图集材料附 coverage / limitations，正文与评论分离，未读部分保留未知。原始截图只用于即时视觉处理，不出现在返回材料中；视觉审计和文字材料仍由部署者按自己的数据规则保存。

发布、点赞、收藏、关注、评论、支付、完整视频播放与指定链接直读不由新端口执行。既有人工评论交接是另一条显式接口，不由此任务自动调用。设备或视觉失败如实返回；需登录时由部署者手动处理，不绕过平台限制。
