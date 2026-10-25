"""日志过滤器 — 静音高频轮询端点的成功访问日志（uvicorn.access）。

心跳 / 状态轮询每几秒一次，200 OK 会淹没控制台，看不到发消息等实质请求。
本过滤器只静音这些端点的 2xx 响应；4xx/5xx 错误照常打印（端点报错仍可诊断）。
挂载见 main.py startup_event 的 addFilter。
"""
import logging

# 静音集合：高频轮询端点前缀（命中 + 2xx → 不打日志）
_MUTED_POLL_PREFIXES = (
    "/api/mirrow/heartbeat",   # 前台可见性心跳，每 3s
    "/api/wander/status",      # 漫想状态，每 5s
    "/api/topic/status/",      # 话题状态轮询，每 10s
    "/api/system/task-status", # 后台任务进度，每 2s（仅任务运行时）
)


class HeartbeatAccessFilter(logging.Filter):
    """过滤 uvicorn.access 中轮询端点的 2xx 成功日志，保留错误。

    uvicorn.access 的 LogRecord.args 是元组:
        (client_addr, method, full_path, http_version, status_code)
    结构不符 / 异常一律放行（宁可少静音，不可误吞错误）。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if not args or len(args) < 5:
                return True
            path = str(args[2]).split("?", 1)[0]
            status = args[4]
            if isinstance(status, int) and 200 <= status < 300:
                for prefix in _MUTED_POLL_PREFIXES:
                    if path.startswith(prefix):
                        return False
        except Exception:
            # 任何解析异常都放行，避免误吞错误日志
            return True
        return True
