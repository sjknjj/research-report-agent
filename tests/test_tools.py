"""检索工具与导出辅助函数测试。"""
import unittest

from src.common.schema import SearchResult
from src.tools.fetcher import extract_main_text
from src.tools.search import DuckDuckGoSearchProvider, MockSearchProvider


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


if __name__ == "__main__":
    unittest.main()
