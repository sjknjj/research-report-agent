"""MCPSource（MCP 客户端数据源）离线测试。

全程用假的 ``urllib.request.urlopen`` 模拟外部 MCP Server，**不联网**：
- 记录完整 JSON-RPC 请求序列，验证 initialize / notifications/initialized / tools/call；
- 覆盖正常取数、JSON-RPC error、未配置 URL、连接失败/超时、非 JSON 响应等容灾路径。
"""
from __future__ import annotations

import json
import os
import unittest
import urllib.error
from unittest import mock

import pandas as pd

from src.data.cleaner import clean_financial, clean_info, clean_price
from src.data.manager import build_manager_from_config, build_manager_from_settings
from src.data.sources import MCPSource, DataSourceError, build_data_source


class _FakeResponse:
    """urllib 响应的最小替身。"""

    def __init__(self, body: bytes):
        self._body = body

    def read(self, n: int = -1) -> bytes:  # noqa: ARG002 - 签名兼容
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeMCPServer:
    """按 JSON-RPC 方法分派的假 MCP Server，并记录请求序列。"""

    def __init__(
        self,
        tool_payloads: dict | None = None,
        tool_error: dict | None = None,
        init_error: dict | None = None,
        raw_text: str | None = None,
        bad_response_text: str | None = None,
        fail: BaseException | None = None,
    ):
        self.tool_payloads = tool_payloads or {}
        self.tool_error = tool_error
        self.init_error = init_error
        self.raw_text = raw_text
        self.bad_response_text = bad_response_text
        self.fail = fail
        self.requests: list[dict] = []
        self.timeouts: list[float] = []

    # -- 内部工具 -----------------------------------------------------------
    def _result(self, payload: dict, result: dict) -> _FakeResponse:
        body = {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
        return _FakeResponse(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def _error(self, payload: dict, error: dict) -> _FakeResponse:
        body = {"jsonrpc": "2.0", "id": payload.get("id"), "error": error}
        return _FakeResponse(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    # -- urlopen 替身 -------------------------------------------------------
    def __call__(self, request, timeout=None):
        self.timeouts.append(timeout)
        if self.fail is not None:
            raise self.fail

        payload = json.loads(request.data.decode("utf-8"))
        self.requests.append(payload)
        method = payload.get("method")

        if method == "initialize":
            if self.init_error:
                return self._error(payload, self.init_error)
            return self._result(
                payload,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "1.0"},
                },
            )

        if method == "notifications/initialized":
            # 通知：server 通常返回 202 空体，客户端也不应解析
            return _FakeResponse(b"")

        if method == "tools/call":
            if self.bad_response_text is not None:
                return _FakeResponse(self.bad_response_text.encode("utf-8"))
            if self.tool_error:
                return self._error(payload, self.tool_error)
            name = payload.get("params", {}).get("name")
            if self.raw_text is not None:
                return self._result(
                    payload, {"content": [{"type": "text", "text": self.raw_text}]}
                )
            if name not in self.tool_payloads:
                return self._error(
                    payload, {"code": -32602, "message": f"未知工具：{name}"}
                )
            text = json.dumps(self.tool_payloads[name], ensure_ascii=False)
            return self._result(payload, {"content": [{"type": "text", "text": text}]})

        return self._error(payload, {"code": -32601, "message": f"未知方法：{method}"})


class _MCPTestBase(unittest.TestCase):
    server_url = "http://127.0.0.1:8931/mcp"

    def make_source(self, server: _FakeMCPServer, **kwargs) -> MCPSource:
        """构造 MCPSource，并把 urllib.request.urlopen 换成假 server。"""
        kwargs.setdefault("server_url", self.server_url)
        patcher = mock.patch("src.data.sources.urllib.request.urlopen", new=server)
        patcher.start()
        self.addCleanup(patcher.stop)
        return MCPSource(**kwargs)

    @staticmethod
    def methods(server: _FakeMCPServer) -> list[str]:
        return [req.get("method") for req in server.requests]


_PRICE_ROWS = [
    {"date": "2024-01-02", "open": 10.0, "close": 10.5, "high": 10.8, "low": 9.9, "volume": 1000},
    {"date": "2024-01-03", "open": 10.5, "close": 10.2, "high": 10.6, "low": 10.0, "volume": 1200},
]


class HandshakeTest(_MCPTestBase):
    def test_initialize_then_tool_call_in_order(self):
        server = _FakeMCPServer(tool_payloads={"stock_price_daily": _PRICE_ROWS})
        source = self.make_source(server)

        source.price_daily("600519", "20240102", "20240103")

        self.assertEqual(
            self.methods(server),
            ["initialize", "notifications/initialized", "tools/call"],
        )
        init = server.requests[0]
        self.assertEqual(init["params"]["protocolVersion"], "2024-11-05")
        # 通知不带 id
        self.assertNotIn("id", server.requests[1])
        self.assertEqual(server.requests[2]["params"]["name"], "stock_price_daily")
        self.assertEqual(server.timeouts[0], 15.0)

    def test_initialize_only_once_across_calls(self):
        server = _FakeMCPServer(tool_payloads={"stock_price_daily": _PRICE_ROWS})
        source = self.make_source(server)

        source.price_daily("600519", "20240102", "20240103")
        source.price_daily("600519", "20240102", "20240103")

        self.assertEqual(self.methods(server).count("initialize"), 1)
        self.assertEqual(self.methods(server).count("tools/call"), 2)

    def test_custom_tool_prefix_and_timeout(self):
        server = _FakeMCPServer(tool_payloads={"mkt_price_daily": _PRICE_ROWS})
        source = self.make_source(server, tool_prefix="mkt_", timeout=3)

        source.price_daily("600519", "20240102", "20240103")

        self.assertEqual(server.requests[2]["params"]["name"], "mkt_price_daily")
        self.assertEqual(server.timeouts[0], 3.0)

    def test_initialize_error_raises(self):
        server = _FakeMCPServer(init_error={"code": -32000, "message": "握手被拒"})
        source = self.make_source(server)

        with self.assertRaises(DataSourceError) as ctx:
            source.price_daily("600519", "20240102", "20240103")
        self.assertIn("-32000", str(ctx.exception))
        self.assertIn("握手被拒", str(ctx.exception))


class PriceDailyTest(_MCPTestBase):
    def test_returns_dataframe_with_akshare_columns(self):
        server = _FakeMCPServer(tool_payloads={"stock_price_daily": _PRICE_ROWS})
        source = self.make_source(server)

        df = source.price_daily("600519", "20240102", "20240103")

        self.assertIsInstance(df, pd.DataFrame)
        for col in ("日期", "开盘", "收盘", "最高", "最低", "成交量"):
            self.assertIn(col, df.columns)

    def test_cleaned_by_cleaner(self):
        server = _FakeMCPServer(tool_payloads={"stock_price_daily": _PRICE_ROWS})
        source = self.make_source(server)

        cleaned = clean_price(source.price_daily("600519", "20240102", "20240103"))

        self.assertEqual(len(cleaned), 2)
        for col in ("date", "open", "close", "high", "low", "volume"):
            self.assertIn(col, cleaned.columns)
        self.assertEqual(cleaned["close"].iloc[-1], 10.2)

    def test_chinese_columns_are_kept(self):
        rows = [
            {"日期": "2024-01-02", "开盘": 10.0, "收盘": 10.5, "最高": 10.8, "最低": 9.9, "成交量": 1000}
        ]
        server = _FakeMCPServer(tool_payloads={"stock_price_daily": rows})
        source = self.make_source(server)

        df = source.price_daily("600519", "20240102", "20240103")

        self.assertIn("日期", df.columns)
        self.assertNotIn("date", df.columns)


class FinancialAndInfoTest(_MCPTestBase):
    def test_financial_abstract_maps_and_cleans(self):
        rows = [
            {"period": "2024-12-31", "revenue": "1.47亿", "net_profit": "3.18千万", "gross_margin": "42.3"},
            {"period": "2023-12-31", "revenue": "1.20亿", "net_profit": "2.50千万", "gross_margin": "40.1"},
        ]
        server = _FakeMCPServer(tool_payloads={"stock_financial_abstract": rows})
        source = self.make_source(server)

        df = source.financial_abstract("600519")
        self.assertIn("报告期", df.columns)

        cleaned = clean_financial(df)
        self.assertEqual(len(cleaned), 2)
        self.assertAlmostEqual(cleaned.iloc[0]["revenue"], 1.47e8)

    def test_stock_info_becomes_item_value_long_table(self):
        server = _FakeMCPServer(
            tool_payloads={"stock_stock_info": [{"代码": "600519", "名称": "贵州茅台", "行业": "白酒"}]}
        )
        source = self.make_source(server)

        df = source.stock_info("600519")

        self.assertEqual(list(df.columns), ["item", "value"])
        info = clean_info(df)
        self.assertEqual(info["名称"], "贵州茅台")

    def test_stock_info_item_value_passthrough(self):
        server = _FakeMCPServer(
            tool_payloads={"stock_stock_info": [{"item": "行业", "value": "白酒"}]}
        )
        source = self.make_source(server)

        df = source.stock_info("600519")
        self.assertEqual(list(df.columns), ["item", "value"])


class ErrorHandlingTest(_MCPTestBase):
    def test_missing_server_url_raises_clear_error(self):
        source = MCPSource("")

        with mock.patch(
            "src.data.sources.urllib.request.urlopen",
            side_effect=AssertionError("未配置 URL 不应发起网络请求"),
        ):
            for call in (
                lambda: source.price_daily("600519", "20240102", "20240103"),
                lambda: source.financial_abstract("600519"),
                lambda: source.stock_info("600519"),
            ):
                with self.assertRaises(DataSourceError) as ctx:
                    call()
                self.assertIn("MCP_SERVER_URL", str(ctx.exception))

    def test_jsonrpc_error_contains_code_and_message(self):
        server = _FakeMCPServer(tool_error={"code": -32001, "message": "行情工具暂不可用"})
        source = self.make_source(server)

        with self.assertRaises(DataSourceError) as ctx:
            source.price_daily("600519", "20240102", "20240103")
        message = str(ctx.exception)
        self.assertIn("-32001", message)
        self.assertIn("行情工具暂不可用", message)

    def test_connection_failure_raises_with_url(self):
        server = _FakeMCPServer(fail=urllib.error.URLError("连接被拒绝"))
        source = self.make_source(server)

        with self.assertRaises(DataSourceError) as ctx:
            source.price_daily("600519", "20240102", "20240103")
        message = str(ctx.exception)
        self.assertIn(self.server_url, message)
        self.assertIn("连接被拒绝", message)

    def test_timeout_raises(self):
        server = _FakeMCPServer(fail=TimeoutError("timed out"))
        source = self.make_source(server)

        with self.assertRaises(DataSourceError) as ctx:
            source.financial_abstract("600519")
        self.assertIn("timed out", str(ctx.exception))

    def test_non_json_http_response_raises(self):
        server = _FakeMCPServer(bad_response_text="<html>502 Bad Gateway</html>")
        source = self.make_source(server)

        with self.assertRaises(DataSourceError) as ctx:
            source.price_daily("600519", "20240102", "20240103")
        self.assertIn("502 Bad Gateway", str(ctx.exception))

    def test_tool_text_not_json_raises_with_preview(self):
        server = _FakeMCPServer(raw_text="服务器内部错误：数据库连接超时")
        source = self.make_source(server)

        with self.assertRaises(DataSourceError) as ctx:
            source.price_daily("600519", "20240102", "20240103")
        self.assertIn("服务器内部错误", str(ctx.exception))

    def test_missing_content_raises(self):
        class _BadServer(_FakeMCPServer):
            def __call__(self, request, timeout=None):
                payload = json.loads(request.data.decode("utf-8"))
                self.requests.append(payload)
                if payload.get("method") == "initialize":
                    return self._result(payload, {"protocolVersion": "2024-11-05"})
                if payload.get("method") == "notifications/initialized":
                    return _FakeResponse(b"")
                # tools/call 返回缺失 content 的 result
                return self._result(payload, {"foo": "bar"})

        source = self.make_source(_BadServer())
        with self.assertRaises(DataSourceError) as ctx:
            source.stock_info("600519")
        self.assertIn("content", str(ctx.exception))

    def test_empty_tool_data_raises(self):
        server = _FakeMCPServer(tool_payloads={"stock_price_daily": []})
        source = self.make_source(server)

        with self.assertRaises(DataSourceError) as ctx:
            source.price_daily("600519", "20240102", "20240103")
        self.assertIn("空数据", str(ctx.exception))


class FactoryTest(unittest.TestCase):
    def test_build_data_source_mcp_constructs_without_url(self):
        source = build_data_source("mcp")
        self.assertIsInstance(source, MCPSource)
        # 惰性校验：构造成功，未配置 URL 时首次取数给出清晰错误
        with self.assertRaises(DataSourceError):
            source.price_daily("600519", "20240102", "20240103")

    def test_manager_passes_settings_url(self):
        class _Settings:
            data_sources = ["mcp"]
            cross_check_enabled = False
            auto_failover = True
            mcp_server_url = "http://127.0.0.1:8931/mcp"

        manager = build_manager_from_settings(_Settings())
        self.assertIsNotNone(manager)
        self.assertEqual(manager.sources[0].server_url, "http://127.0.0.1:8931/mcp")

    def test_manager_config_env_fallback(self):
        with mock.patch.dict(os.environ, {"MCP_SERVER_URL": "http://env-host/mcp"}):
            manager = build_manager_from_config({"sources": ["mcp"]})
        self.assertEqual(manager.sources[0].server_url, "http://env-host/mcp")

    def test_manager_config_explicit_url_wins(self):
        manager = build_manager_from_config(
            {"sources": ["mcp"], "mcp_server_url": "http://config-host/mcp"}
        )
        self.assertEqual(manager.sources[0].server_url, "http://config-host/mcp")


if __name__ == "__main__":
    unittest.main()
