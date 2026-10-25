"""MCP 音乐客户端 — 通过 stdio 子进程调用 netease-music-mcp

通信协议: JSON-RPC 2.0 over stdio (每行一个 JSON)
参考: ttps_pipeline.py 调 RVC、music_analyzer.py 调 basic-pitch 的 subprocess 模式
"""

import asyncio
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

_MCP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "music_mcp")
_SERVER_JS = os.path.join(_MCP_DIR, "src", "server.js")

_client_instance = None


class MusicMcpClient:
    """MCP stdio 客户端单例"""

    def __init__(self):
        self._proc = None
        self._reader = None
        self._writer = None
        self._lock = asyncio.Lock()
        self._req_id = 0
        self._running = False

    async def start(self):
        """启动 MCP 子进程并完成初始化握手。"""
        if self._running:
            return

        if not os.path.exists(_SERVER_JS):
            logger.warning(f"MCP server.js 不存在: {_SERVER_JS}")
            return

        node = "node"
        try:
            self._proc = await asyncio.create_subprocess_exec(
                node, _SERVER_JS,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=_MCP_DIR,
            )
            self._reader = self._proc.stdout
            self._writer = self._proc.stdin
            self._running = True

            # 发送 initialize 请求（MCP 协议握手）
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mirrow", "version": "1.0"},
            })
            if init_result:
                await self._send_notification("notifications/initialized", {})
                logger.info("MCP 音乐客户端启动成功")
            else:
                logger.warning("MCP 初始化握手失败")
        except FileNotFoundError:
            logger.warning("node 未安装，MCP 不可用")
        except Exception as e:
            logger.error(f"MCP 启动失败: {e}")
            self._running = False

    async def close(self):
        """优雅关闭 MCP 子进程。"""
        if not self._running or not self._proc:
            return
        try:
            await self.call_tool("stop", {})
        except Exception:
            pass
        try:
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._running = False
        logger.info("MCP 音乐客户端已关闭")

    def is_running(self) -> bool:
        return self._running and self._proc is not None and self._proc.returncode is None

    async def call_tool(self, name: str, arguments: dict = None) -> dict:
        """调用 MCP 工具，返回 result 字典。失败返回 {}。"""
        if not self.is_running():
            return {}

        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        if result is None:
            return {}
        # MCP 返回格式: {content: [{type: "text", text: "{...}"}]}
        content = result.get("content", [])
        if content and isinstance(content, list):
            text = content[0].get("text", "") if content else ""
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"_raw": text}
        return result

    async def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求并等待响应。"""
        async with self._lock:
            self._req_id += 1
            req = {
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": method,
                "params": params,
            }
            try:
                line = json.dumps(req, ensure_ascii=False) + "\n"
                self._writer.write(line.encode())
                await self._writer.drain()

                # 读取响应（单行 JSON）
                resp_line = await asyncio.wait_for(
                    self._reader.readline(), timeout=30.0
                )
                resp = json.loads(resp_line.decode().strip())
                if "error" in resp:
                    logger.warning(f"MCP error: {resp['error']}")
                    return None
                return resp.get("result", {})
            except asyncio.TimeoutError:
                logger.warning(f"MCP timeout: {method}")
                return None
            except Exception as e:
                logger.error(f"MCP 通信异常: {e}")
                return None

    async def _send_notification(self, method: str, params: dict):
        """发送 JSON-RPC 通知（无响应）。"""
        if not self._writer:
            return
        async with self._lock:
            try:
                notif = {"jsonrpc": "2.0", "method": method, "params": params}
                line = json.dumps(notif, ensure_ascii=False) + "\n"
                self._writer.write(line.encode())
                await self._writer.drain()
            except Exception:
                pass


def get_mcp_client() -> MusicMcpClient:
    """获取全局 MCP 客户端单例。"""
    global _client_instance
    if _client_instance is None:
        _client_instance = MusicMcpClient()
    return _client_instance


async def start_mcp_client():
    """启动 MCP 客户端（在 behavior_scheduler 初始化后调用）。"""
    client = get_mcp_client()
    await client.start()


async def stop_mcp_client():
    """关闭 MCP 客户端（在 shutdown 时调用）。"""
    client = get_mcp_client()
    await client.close()
