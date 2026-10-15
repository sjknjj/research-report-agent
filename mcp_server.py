#!/usr/bin/env python3
"""MCP stdio 服务器：把 research-report-agent 作为 MCP 工具接入 Claude Desktop。

为什么需要这个文件？
    Claude Desktop 的 ``url`` 配置要求 **Streamable HTTP**（session + GET/SSE），
    而 ``web/app.py`` 的 ``POST /mcp`` 只是一个裸 JSON-RPC over HTTP 端点，
    无法直接满足其握手要求。stdio 传输更通用也更简单：客户端通过子进程的
    stdin/stdout 逐行收发 JSON-RPC，本项目已在 ``src/mcp/server.py`` 实现好协议逻辑。

协议：
    - 从 stdin 逐行读取 JSON-RPC 请求；
    - 经 ``MCPServer.handle`` 处理后，把响应写到 stdout（每行一个 JSON）；
    - ``notifications/*`` 是通知，**不回任何响应**；
    - 支持 ``initialize`` / ``ping`` / ``tools/list`` / ``tools/call``；
    - 所有日志走 stderr，stdout 只输出协议 JSON，绝不混入日志。

Claude Desktop 配置示例（``claude_desktop_config.json``）：
    macOS: ~/Library/Application Support/Claude/claude_desktop_config.json
    Windows: %APPDATA%\\Claude\\claude_desktop_config.json

    {
      "mcpServers": {
        "research-report-agent": {
          "command": "python3",
          "args": [
            "/absolute/path/to/research-report-agent/mcp_server.py"
          ]
        }
      }
    }

    把 ``/absolute/path/to/research-report-agent`` 换成本仓库的绝对路径；
    Windows 上 ``command`` 可写 ``python``，路径用反斜杠或正斜杠均可。
    若依赖装在虚拟环境里，``command`` 指向该环境的 python 可执行文件即可。

手动验证：
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 mcp_server.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 保证从任意工作目录启动都能导入 src 包
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _log(message: str) -> None:
    """日志一律写 stderr，避免污染 stdout 的协议流。"""
    print(message, file=sys.stderr, flush=True)


def _write(response: dict) -> None:
    """把一个 JSON-RPC 响应写到 stdout（一行一个 JSON）。"""
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def main() -> int:
    from src.common.config import load_settings
    from src.common.logger import setup_logging
    from src.mcp.server import MCPServer

    settings = load_settings()
    setup_logging(settings.log_level)
    _log("[mcp_server] stdio MCP 服务器已启动，等待 JSON-RPC 请求…")

    server = MCPServer(settings)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(_error(None, -32700, f"JSON 解析失败：{exc}"))
            continue
        if not isinstance(request, dict):
            _write(_error(None, -32600, "请求必须是 JSON-RPC 对象"))
            continue

        try:
            response = server.handle(request)
        except Exception as exc:  # noqa: BLE001 - 绝不让单条请求打断整个 stdio 循环
            _log(f"[mcp_server] 处理请求异常：{exc}")
            response = _error(request.get("id"), -32603, str(exc))

        # 通知（notifications/*）返回 None：按规范不回响应
        if response is not None:
            _write(response)

    _log("[mcp_server] stdin 已关闭，退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
