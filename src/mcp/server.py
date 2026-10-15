"""MCP Server（3.0新增）：把本项目核心能力暴露为标准 MCP 工具。

遵循 Model Context Protocol 基本规范（JSON-RPC 2.0），
实现了 ``initialize`` / ``notifications/initialized`` / ``ping`` / ``tools/list`` / ``tools/call``，
Claude Desktop / Cursor 等支持 MCP 的客户端可完成握手并接入本项目作为工具使用。

暴露的工具：
- get_stock_brief：获取 A 股公司投研速览（含反幻觉校验）
- verify_numbers：数字交叉校验（反幻觉）
- generate_stock_report：生成完整 A 股研究报告（多智能体流水线）
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..common.config import Settings, load_settings
from ..common.logger import get_logger
from ..services.research_assistant import ResearchAssistant

logger = get_logger("mcp.server")

# 对外声明的 MCP 协议版本与服务器信息
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "research-report-agent", "version": "3.7.0"}


# MCP 工具声明（符合 MCP tools/list 格式）
TOOLS_DECLARATION = [
    {
        "name": "get_stock_brief",
        "description": "获取 A 股指定股票代码的投研速览：行情、财务、资讯、技术面指标，含数字反幻觉交叉校验。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "A 股代码，如 600519"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "verify_numbers",
        "description": "对一段文本中的数字声明做交叉校验，找出疑似幻觉的数字（与真实财务数据比对）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待校验的文本"},
                "symbol": {"type": "string", "description": "股票代码，用于加载事实库"},
            },
            "required": ["text", "symbol"],
        },
    },
    {
        "name": "generate_stock_report",
        "description": "生成完整的 A 股研究报告（多智能体：规划→检索→分析→多空辩论→撰写→审校）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "研究主题，如 白酒行业深度分析"},
                "deep": {"type": "boolean", "description": "是否启用递归深挖检索"},
            },
            "required": ["topic"],
        },
    },
]

# 工具名 -> 必填参数（用于 tools/call 参数校验）
_REQUIRED_ARGS = {
    tool["name"]: list(tool["inputSchema"].get("required") or [])
    for tool in TOOLS_DECLARATION
}


class JSONRPCError(Exception):
    """带 JSON-RPC 错误码的异常。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class MCPServer:
    """MCP Server：处理 JSON-RPC 格式的工具调用请求。"""

    def __init__(self, settings: Settings = None):
        self.settings = settings or load_settings()
        self.assistant = ResearchAssistant(self.settings)

    def handle(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理单个 JSON-RPC 请求。

        返回响应字典；对通知类方法（``notifications/*``）返回 ``None``，
        表示按 JSON-RPC / MCP 规范**不发送任何响应**。
        """
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params") or {}

        try:
            if method == "initialize":
                return self._ok(req_id, {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                })
            if method.startswith("notifications/"):
                # 通知没有 id，也绝不能回响应
                logger.debug("收到 MCP 通知：%s", method)
                return None
            if method == "tools/list":
                return self._ok(req_id, {"tools": TOOLS_DECLARATION})
            if method == "tools/call":
                return self._ok(req_id, self._call_tool(params))
            if method == "ping":
                # MCP 规范要求 ping 返回空对象
                return self._ok(req_id, {})
            return self._error(req_id, -32601, f"未知方法：{method}")
        except JSONRPCError as exc:
            return self._error(req_id, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP 调用异常：%s", exc)
            return self._error(req_id, -32603, str(exc))

    # ------------------------------------------------------------------
    def _call_tool(self, params: dict) -> dict:
        if not isinstance(params, dict):
            raise JSONRPCError(-32602, "params 必须是对象")
        name = params.get("name", "")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise JSONRPCError(-32602, "arguments 必须是对象")
        if name not in _REQUIRED_ARGS:
            raise JSONRPCError(-32602, f"未知工具：{name}")

        missing = [key for key in _REQUIRED_ARGS[name] if not arguments.get(key)]
        if missing:
            raise JSONRPCError(-32602, f"参数不合法：缺少/为空 {', '.join(missing)}")

        handlers = {
            "get_stock_brief": self._tool_brief,
            "verify_numbers": self._tool_verify,
            "generate_stock_report": self._tool_report,
        }
        try:
            result = handlers[name](arguments)
        except Exception as exc:  # noqa: BLE001 - 工具执行失败用 isError 表达，不中断服务
            logger.warning("MCP 工具 %s 执行失败：%s", name, exc)
            return {
                "content": [{"type": "text", "text": f"工具执行失败：{exc}"}],
                "isError": True,
            }
        result.setdefault("isError", False)
        return result

    def _tool_brief(self, args: dict) -> dict:
        symbol = args.get("symbol", "")
        result = self.assistant.company_brief(symbol, prefer_live=True)
        return {
            "content": [{"type": "text", "text": result.text}],
            "verification": result.verification,
        }

    def _tool_verify(self, args: dict) -> dict:
        from ..verify.facts import FactBase
        from ..verify.verifier import CrossVerifier
        from ..data.data_service import DataService

        text = args.get("text", "")
        symbol = args.get("symbol", "")
        bundle = DataService().get_company_bundle(symbol, prefer_live=True)
        fact_base = FactBase(bundle)
        report = CrossVerifier().verify(text, fact_base)
        return {
            "content": [{"type": "text", "text": report.render_text()}],
            "verification": report.to_dict(),
        }

    def _tool_report(self, args: dict) -> dict:
        from ..agent.orchestrator import ResearchOrchestrator

        topic = args.get("topic", "")
        deep = args.get("deep", False)
        orchestrator = ResearchOrchestrator(self.settings)
        report = orchestrator.run(topic, deep=deep)
        text = f"# {report.topic}\n\n{report.abstract}\n\n"
        for s in report.sections:
            text += f"## {s.heading}\n{s.content}\n\n"
        return {
            "content": [{"type": "text", "text": text}],
            "sections": len(report.sections),
            "references": len(report.references),
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _ok(req_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }
