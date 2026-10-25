"""MCP stdio/loopback server for the independent Meituan domain."""

from __future__ import annotations

import argparse
import asyncio
import json

from commerce_shared.mcp_protocol import McpJsonRpcDispatcher, McpToolSpec, serve_loopback, serve_stdio
try:
    from .service import MeituanOrderService
except ImportError:  # direct ``python server.py`` development entrypoint
    from meituan_order_mcp.service import MeituanOrderService


def build_dispatcher(service: MeituanOrderService | None = None) -> McpJsonRpcDispatcher:
    service = service or MeituanOrderService()
    specs = (
        McpToolSpec("submit_request", "提交结构化美团配送需求；仅接收收藏店铺范围内的需求 brief。", {"type": "object", "required": ["brief", "max_payable"], "properties": {"brief": {"type": "string", "maxLength": 1000}, "max_payable": {"type": ["number", "string"]}, "address_label": {"type": "string", "enum": ["default", "home", "work"]}, "constraints": {"type": "object"}}}),
        McpToolSpec("get_proposal", "获取含优惠后实付的紧凑候选证据卡。", {"type": "object", "required": ["request_id"], "properties": {"request_id": {"type": "string"}}}),
        McpToolSpec("confirm_candidate", "确认一个收藏店铺候选；不执行付款。", {"type": "object", "required": ["request_id", "candidate_id"], "properties": {"request_id": {"type": "string"}, "candidate_id": {"type": "string"}, "repeated_principle": {"type": "boolean"}, "explicit_persistent": {"type": "boolean"}}}),
        McpToolSpec("get_status", "读取配送任务状态与执行能力边界。", {"type": "object", "required": ["request_id"], "properties": {"request_id": {"type": "string"}}}),
        McpToolSpec("cancel_request", "取消尚未完成的配送任务。", {"type": "object", "required": ["request_id"], "properties": {"request_id": {"type": "string"}}}),
    )
    return McpJsonRpcDispatcher(
        name="meituan-order-mcp", version="1.0.0", tools=specs,
        handlers={
            "submit_request": lambda args: service.submit_request(args),
            "get_proposal": lambda args: service.get_proposal(str(args.get("request_id") or "")),
            "confirm_candidate": lambda args: service.confirm_candidate(str(args.get("request_id") or ""), str(args.get("candidate_id") or ""), repeated_principle=bool(args.get("repeated_principle")), explicit_persistent=bool(args.get("explicit_persistent"))),
            "get_status": lambda args: service.get_status(str(args.get("request_id") or "")),
            "cancel_request": lambda args: service.cancel_request(str(args.get("request_id") or "")),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    dispatcher = build_dispatcher()
    if args.http:
        server = serve_loopback(dispatcher, host="127.0.0.1", port=args.port)
        print(json.dumps({"host": "127.0.0.1", "port": server.server_port, "path": "/mcp"}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    else:
        asyncio.run(serve_stdio(dispatcher))


if __name__ == "__main__":
    main()
