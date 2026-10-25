#!/usr/bin/env python3
"""
上下文调度器MCP服务器
通过stdio提供JSON-RPC MCP工具（简化实现，无外部mcp库依赖）
"""
import asyncio
import json
import sys
import os
from typing import Any, Dict

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import TOOLS, TOOL_HANDLERS, get_scheduler

async def handle_tool_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """处理工具调用"""
    try:
        if tool_name not in TOOL_HANDLERS:
            return {"error": f"未知工具: {tool_name}"}

        handler = TOOL_HANDLERS[tool_name]
        result = await handler(**arguments)

        if isinstance(result, dict):
            return {"result": result}
        else:
            return {"result": json.loads(json.dumps(result, default=str))}

    except TypeError as e:
        return {"error": f"参数错误: {str(e)}", "type": "TypeError"}
    except ValueError as e:
        return {"error": f"值错误: {str(e)}", "type": "ValueError"}
    except Exception as e:
        return {"error": str(e), "type": type(e).__name__}


if __name__ == "__main__":
    # 简化版本：直接处理stdio上的MCP协议
    # 由于mcp库可能版本不同，我们实现一个简化的stdio服务器
    import sys
    import json

    async def simple_mcp_server():
        """简化的MCP服务器，通过stdio通信"""
        # 初始化调度器
        try:
            scheduler = await get_scheduler()
            print(f"[初始化] 上下文调度器状态: {scheduler.initialized}", file=sys.stderr)
        except Exception as e:
            print(f"[初始化] 调度器初始化失败: {e}", file=sys.stderr)

        # 读取初始化请求
        init_request = json.loads(sys.stdin.readline())
        if init_request.get("method") != "initialize":
            print(json.dumps({"error": "期望初始化请求"}), file=sys.stderr)
            return

        # 发送初始化响应
        init_response = {
            "jsonrpc": "2.0",
            "id": init_request["id"],
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "context-scheduler-mcp",
                    "version": "1.0.0"
                }
            }
        }
        print(json.dumps(init_response), flush=True)

        # 处理消息循环
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                request = json.loads(line)
                method = request.get("method")
                request_id = request.get("id")

                if method == "tools/list":
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "tools": TOOLS
                        }
                    }
                    print(json.dumps(response), flush=True)

                elif method == "tools/call":
                    # 处理工具调用
                    params = request.get("params", {})
                    tool_name = params.get("name")
                    arguments = params.get("arguments", {})

                    # 调用工具处理函数
                    result = await handle_tool_call(tool_name, arguments)

                    # 发送结果
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(result, ensure_ascii=False)
                                }
                            ]
                        }
                    }
                    print(json.dumps(response), flush=True)

                elif method == "shutdown":
                    # 清理资源
                    try:
                        scheduler = await get_scheduler()
                        await scheduler.close()
                    except Exception:
                        pass
                    break

                elif method == "ping":
                    # 健康检查
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "status": "alive",
                            "service": "context-scheduler-mcp"
                        }
                    }
                    print(json.dumps(response), flush=True)

            except json.JSONDecodeError as e:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": request_id if 'request_id' in locals() else None,
                    "error": {
                        "code": -32700,
                        "message": f"JSON解析错误: {str(e)}"
                    }
                }
                print(json.dumps(error_response), flush=True)

            except Exception as e:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": request_id if 'request_id' in locals() else None,
                    "error": {
                        "code": -32603,
                        "message": str(e)
                    }
                }
                print(json.dumps(error_response), flush=True)

    # 运行简化服务器
    try:
        asyncio.run(simple_mcp_server())
    except KeyboardInterrupt:
        print("服务器被用户中断", file=sys.stderr)
    except Exception as e:
        print(f"服务器运行错误: {e}", file=sys.stderr)