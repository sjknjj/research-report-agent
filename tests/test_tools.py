"""检索工具、抓取、通知、RAG 与 MCP 协议测试。"""
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from src.common.schema import SearchResult
from src.tools.fetcher import extract_main_text, fetch_page
from src.tools.search import (
    BaseSearchProvider,
    DuckDuckGoSearchProvider,
    MockSearchProvider,
    MultiSearchProvider,
    SearchTool,
    build_search_tool,
)


class SearchToolTest(unittest.TestCase):
    def test_mock_provider_is_labeled(self):
        results = MockSearchProvider().search("示例", max_results=3)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertTrue(r.title.startswith("[离线占位"))
            self.assertIn("example.com", r.url)

    def test_ddg_redirect_decode(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.example.com%2Fa&rut=abc"
        self.assertEqual(
            DuckDuckGoSearchProvider._decode_redirect(href),
            "https://www.example.com/a",
        )

    def test_redirect_protocol_relative(self):
        self.assertEqual(
            DuckDuckGoSearchProvider._decode_redirect("//example.com/a"),
            "https://example.com/a",
        )

    def test_redirect_empty_uddg_is_dropped(self):
        self.assertEqual(
            DuckDuckGoSearchProvider._decode_redirect("//duckduckgo.com/l/?uddg=&rut=abc"),
            "",
        )

    def test_max_total_zero_means_empty(self):
        tool = SearchTool(MockSearchProvider(), per_query=3)
        self.assertEqual(tool.search_many(["a", "b"], max_total=0), [])

    def test_search_one_catches_non_network_error(self):
        class Boom(BaseSearchProvider):
            name = "boom"

            def search(self, query, max_results=5):
                raise KeyError("意外异常也不能穿透")

        results = SearchTool(Boom(), per_query=2).search_one("q")
        self.assertTrue(results)
        self.assertEqual(results[0].source, "mock")

    def test_multi_provider_falls_back_and_sticks(self):
        class Boom(BaseSearchProvider):
            name = "boom"

            def search(self, query, max_results=5):
                raise ValueError("boom")

        class Ok(BaseSearchProvider):
            name = "ok"

            def search(self, query, max_results=5):
                return [SearchResult(title="t", url="https://x/1", snippet="", source="ok", query=query)]

        provider = MultiSearchProvider([Boom(), Ok()])
        results = provider.search("q")
        self.assertEqual(len(results), 1)
        self.assertEqual(provider._active.name, "ok")

    def test_auto_construction_does_not_probe_network(self):
        from src.common.config import load_settings

        settings = load_settings(cli_overrides={"mode": "online", "search_provider": "auto"})
        settings.effective_mode = "online"
        with mock.patch(
            "src.tools.search.urllib.request.urlopen",
            side_effect=AssertionError("构造期不应发起网络请求"),
        ):
            tool = build_search_tool(settings)
        self.assertIsInstance(tool.provider, MultiSearchProvider)


class _FakeResponse:
    """urllib 响应的最小替身。"""

    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = headers

    def read(self, n: int = -1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FetcherTest(unittest.TestCase):
    def test_extract_main_text(self):
        html = """
        <html><head><style>.x{color:red}</style></head>
        <body><script>var a=1;</script>
        <p>这是一段足够长的正文内容，用于测试正文抽取是否正常工作。</p>
        <div>这是另一段也足够长的正文，用于验证块级标签的处理。</div>
        </body></html>
        """
        text = extract_main_text(html)
        self.assertIn("正文", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("var a", text)

    def test_corrupted_gzip_returns_empty(self):
        """损坏 gzip 也不能抛 zlib/BadGzipFile，必须返回空串。"""
        bad = b"\x1f\x8b" + b"not-a-real-gzip-stream"
        resp = _FakeResponse(bad, {"Content-Type": "text/html; charset=utf-8"})
        with mock.patch("src.tools.fetcher.urllib.request.urlopen", return_value=resp):
            self.assertEqual(fetch_page("https://example.com/a.html"), "")

    def test_gbk_body_not_mojibake(self):
        from src.tools.fetcher import _decode_body

        raw = "营业总收入152.30亿元".encode("gbk")
        text = _decode_body(raw, "text/html; charset=gbk")
        self.assertIn("营业总收入", text)

    def test_gb18030_fallback_without_charset(self):
        from src.tools.fetcher import _decode_body

        raw = "营业总收入152.30亿元".encode("gb18030")
        text = _decode_body(raw, "text/html")
        self.assertIn("营业总收入", text)


class NotifyTest(unittest.TestCase):
    def test_wecom_send_markdown_signature(self):
        from src.notify.wecom import WeComNotifier

        notifier = WeComNotifier("key")
        captured = {}
        notifier._send = lambda payload: captured.update(payload) or True
        self.assertTrue(notifier.send_markdown("标题", "正文"))
        content = captured["markdown"]["content"]
        self.assertIn("标题", content)
        self.assertIn("正文", content)

    def test_telegram_escapes_markdown_v2(self):
        from src.notify.telegram import _escape_md_v2

        self.assertEqual(_escape_md_v2("a_b*c"), "a\\_b\\*c")

    def test_channels_are_isolated(self):
        """企业微信抛异常不应连带中断 Telegram。"""
        from src import notify

        calls = []

        class Bad:
            def __init__(self, *args, **kwargs):
                pass

            def send_markdown(self, *args, **kwargs):
                raise TypeError("模拟企微签名错误")

        class Good:
            def __init__(self, *args, **kwargs):
                pass

            def send_markdown(self, *args, **kwargs):
                calls.append("telegram")
                return True

        with mock.patch.object(notify, "WeComNotifier", Bad), mock.patch.object(
            notify, "TelegramNotifier", Good
        ):
            notify.push_brief_result(
                "t", "x", {}, wecom_webhook_key="k",
                telegram_bot_token="tok", telegram_chat_id="1",
            )
        self.assertEqual(calls, ["telegram"])


class EmbeddingTest(unittest.TestCase):
    def test_single_char_vector_is_nonzero(self):
        from src.rag.embedding import _hash_vec

        self.assertGreater(float(np.linalg.norm(_hash_vec("a"))), 0.0)

    def test_dimension_stays_consistent(self):
        from src.rag.embedding import HASH_DIM, Embedder

        embedder = Embedder(base_url="", api_key="")
        self.assertEqual(embedder.embed_one("abc").size, embedder.embed_one("abcdef").size)
        self.assertEqual(embedder.dim, HASH_DIM)

    def test_api_locked_failure_raises_instead_of_mismatch(self):
        from src.rag.embedding import Embedder, EmbeddingError

        embedder = Embedder(base_url="", api_key="")
        embedder.lock_mode(use_api=True, dim=1536)
        with self.assertRaises(EmbeddingError):
            embedder.embed_one("abc")


class VectorStoreTest(unittest.TestCase):
    def test_add_many_keeps_metadata_and_persists_vectors(self):
        from src.rag.vector_store import VectorStore

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "idx.json")
            store = VectorStore(persist_path=path)
            store.add_many(
                ["苹果公司营收增长", "白酒行业深度分析"],
                metadatas=[{"k": 1}, {"k": 2}],
            )
            self.assertEqual(store.docs[0].metadata, {"k": 1})
            store.save()

            reloaded = VectorStore(persist_path=path)
            self.assertEqual(len(reloaded.docs), 2)
            self.assertEqual(reloaded.docs[1].metadata, {"k": 2})
            self.assertEqual(len(reloaded.vectors), 2)
            self.assertTrue(reloaded.search("白酒行业", top_k=1))


class MCPProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.common.config import load_settings
        from src.mcp.server import MCPServer

        cls.server = MCPServer(
            load_settings(cli_overrides={"mode": "offline", "search_provider": "mock"})
        )

    def test_initialize_handshake(self):
        resp = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(resp["id"], 1)
        self.assertIn("protocolVersion", resp["result"])
        self.assertEqual(resp["result"]["serverInfo"]["name"], "research-report-agent")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_notification_has_no_response(self):
        resp = self.server.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertIsNone(resp)

    def test_ping_returns_empty_object(self):
        resp = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_tools_call_invalid_params(self):
        resp = self.server.handle(
            {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "get_stock_brief", "arguments": {}},
            }
        )
        self.assertEqual(resp["error"]["code"], -32602)

    def test_tools_call_unknown_tool(self):
        resp = self.server.handle(
            {
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "not_a_tool", "arguments": {}},
            }
        )
        self.assertEqual(resp["error"]["code"], -32602)

    def test_unknown_method(self):
        resp = self.server.handle({"jsonrpc": "2.0", "id": 5, "method": "no/such"})
        self.assertEqual(resp["error"]["code"], -32601)

    def test_tools_call_result_has_is_error_false(self):
        """成功结果带 isError=False（patch 掉真实取数，保持测试离线）。"""
        with mock.patch.object(
            self.server, "_tool_brief",
            return_value={"content": [{"type": "text", "text": "ok"}]},
        ):
            resp = self.server.handle(
                {
                    "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                    "params": {"name": "get_stock_brief", "arguments": {"symbol": "000000"}},
                }
            )
        self.assertIn("result", resp)
        self.assertFalse(resp["result"]["isError"])

    def test_tools_call_execution_error_sets_is_error(self):
        with mock.patch.object(self.server, "_tool_brief", side_effect=RuntimeError("boom")):
            resp = self.server.handle(
                {
                    "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                    "params": {"name": "get_stock_brief", "arguments": {"symbol": "000000"}},
                }
            )
        self.assertIn("result", resp)
        self.assertTrue(resp["result"]["isError"])

    def test_docstring_tool_names_match_declaration(self):
        from src.mcp.server import TOOLS_DECLARATION

        declared = {t["name"] for t in TOOLS_DECLARATION}
        self.assertEqual(
            declared,
            {"get_stock_brief", "verify_numbers", "generate_stock_report", "search_web"},
        )

    def test_search_web_offline_falls_back_to_mock(self):
        """离线模式下 search_web 必须返回占位结果而不是报错。"""
        resp = self.server.handle(
            {
                "jsonrpc": "2.0", "id": 8, "method": "tools/call",
                "params": {
                    "name": "search_web",
                    "arguments": {"query": "低空经济 产业链", "max_results": 2},
                },
            }
        )
        result = resp["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["count"], 2)
        # 结构化结果须含标题/链接/来源
        for item in result["results"]:
            self.assertTrue(item["title"])
            self.assertTrue(item["url"])
            self.assertEqual(item["source"], "mock")
        self.assertIn("content", result)

    def test_search_web_invalid_max_results_returns_32602(self):
        resp = self.server.handle(
            {
                "jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": "search_web", "arguments": {"query": "x", "max_results": 0}},
            }
        )
        self.assertEqual(resp["error"]["code"], -32602)

    def test_search_web_missing_query_returns_32602(self):
        resp = self.server.handle(
            {
                "jsonrpc": "2.0", "id": 10, "method": "tools/call",
                "params": {"name": "search_web", "arguments": {}},
            }
        )
        self.assertEqual(resp["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
