"""导出层 / CLI / 日志的回归测试。

覆盖审查报告中负责的修复点：
- Markdown 表格渲染为 <table>
- 内联 Markdown 的 XSS 防护
- 核对表单元格转义（竖线、换行）
- PDF 缺中文字体时明确失败
- plotly.js 只注入一次且不依赖 CDN
- CLI 注册 stats、serve --port
- 日志输出到 stderr 且不破坏宿主 handler
"""
import logging
import sys
import tempfile
import unittest
from pathlib import Path

from src.common.logger import get_logger, setup_logging
from src.export.exporter import (
    _inline_md,
    build_assistant_markdown,
    export_assistant,
    markdown_to_html,
)


class MarkdownToHtmlTest(unittest.TestCase):
    def test_table_rendered(self):
        md = (
            "| 指标 | 状态 | 核对说明 |\n"
            "| --- | --- | --- |\n"
            "| 营业收入 | ✓ 一致 | 文本 12.5% ≈ 事实 12.5% |\n"
            "| 毛利率 | ✗ 矛盾 | 文本 50% vs 事实 42.3% |\n"
        )
        html = markdown_to_html(md)
        self.assertEqual(html.count("<table"), 1)
        self.assertEqual(html.count("<th>"), 3)
        self.assertEqual(html.count("<td>"), 6)
        self.assertIn("营业收入", html)
        self.assertIn("</table>", html)
        # 不能再退化成带竖线的段落
        self.assertNotIn("<p>|", html)

    def test_inline_md_blocks_attribute_injection(self):
        out = _inline_md('[x](https://evil" onmouseover="alert(1))')
        self.assertNotIn('onmouseover="alert', out)
        # 转义后的引号即便进入 href 也无法闭合属性
        if "<a " in out:
            self.assertNotIn('href="https://evil"', out)

    def test_inline_md_rejects_non_http_scheme(self):
        out = _inline_md("[点我](javascript:alert(1))")
        self.assertNotIn("<a", out)
        self.assertNotIn("javascript:", out)

    def test_inline_md_valid_link(self):
        out = _inline_md("[官网](https://example.com/a?b=1&c=2)")
        self.assertIn('href="https://example.com/a?b=1&amp;c=2"', out)
        self.assertIn('rel="noopener noreferrer"', out)

    def test_claims_cells_escape_pipe_and_newline(self):
        verification = {
            "passed": True,
            "total_claims": 1,
            "supported": 1,
            "mismatch": 0,
            "unverifiable": 0,
            "hallucination_rate": 0.0,
            "claims": [
                {"status": "supported", "label": "a|b\nc", "note": "x|y\nz"},
            ],
        }
        md = build_assistant_markdown("标题", "正文", verification=verification)
        row = [line for line in md.splitlines() if line.startswith("| a")][0]
        # 首尾两个竖线 + 两个列分隔 = 4 个，行内不能有裸竖线或换行
        self.assertEqual(row.count("|"), 4)
        self.assertIn("a／b c", row)
        self.assertIn("x／y z", row)


class PdfFailureTest(unittest.TestCase):
    def test_missing_cjk_font_raises(self):
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab 未安装")
        from src.export import pdf_export

        original = pdf_export._find_cjk_font
        pdf_export._find_cjk_font = lambda: None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(pdf_export.PdfExportError):
                    pdf_export.export_pdf("# 标题\n\n中文内容", str(Path(tmp) / "x.pdf"))
        finally:
            pdf_export._find_cjk_font = original

    def test_export_assistant_propagates_pdf_error(self):
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab 未安装")
        from src.export import pdf_export

        original = pdf_export._find_cjk_font
        pdf_export._find_cjk_font = lambda: None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(pdf_export.PdfExportError) as ctx:
                    export_assistant("标题", "正文", tmp, formats=("md", "html", "pdf"))
                # 已生成的 md/html 必须通过异常对象暴露出来
                written = getattr(ctx.exception, "written", [])
                self.assertEqual(len(written), 2)
                self.assertTrue(all(p.exists() for p in written))
        finally:
            pdf_export._find_cjk_font = original


class PlotlyInjectionTest(unittest.TestCase):
    def test_inject_once_offline(self):
        try:
            import numpy as np
            import pandas as pd
            import plotly  # noqa: F401
        except ImportError:
            self.skipTest("plotly/pandas 未安装")
        from src.export.charts import ensure_plotly_js, financial_trend_chart, kline_with_volume

        n = 12
        price_df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n),
            "open": np.arange(n) + 1.0,
            "close": np.arange(n) + 1.5,
            "high": np.arange(n) + 2.0,
            "low": np.arange(n) + 0.5,
            "volume": np.arange(n) * 100 + 10,
        })
        kline = kline_with_volume(price_df)
        self.assertTrue(kline)
        self.assertNotIn('src="https://cdn.plot.ly', kline)

        fin_df = pd.DataFrame({
            "period": [f"2023Q{i}" for i in range(1, 6)],
            "revenue": [1e9, 1.1e9, 1.2e9, 1.3e9, 1.4e9],
            "net_profit": [1e8, 1.1e8, 1.2e8, 1.3e8, 1.4e8],
        })
        trend = financial_trend_chart(fin_df)
        self.assertTrue(trend)

        combined = kline + trend
        injected = ensure_plotly_js(combined)
        self.assertTrue(injected.startswith("<script>"))
        self.assertNotIn('src="https://cdn.plot.ly', injected)
        # 再次注入应保持幂等
        self.assertEqual(len(ensure_plotly_js(injected)), len(injected))


class CliParserTest(unittest.TestCase):
    def test_stats_registered(self):
        from main import build_parser

        args = build_parser().parse_args(["stats"])
        self.assertEqual(args.command, "stats")

    def test_serve_port(self):
        from main import build_parser

        parser = build_parser()
        self.assertEqual(parser.parse_args(["serve"]).port, 8000)
        self.assertEqual(parser.parse_args(["serve", "--port", "8080"]).port, 8080)


class LoggerIsolationTest(unittest.TestCase):
    def setUp(self):
        self.root = logging.getLogger()
        self._saved_handlers = list(self.root.handlers)
        self._saved_level = self.root.level

    def tearDown(self):
        self.root.handlers[:] = self._saved_handlers
        self.root.setLevel(self._saved_level)

    def test_setup_logging_adds_stderr_handler(self):
        self.root.handlers.clear()

        setup_logging("DEBUG")

        streams = [getattr(h, "stream", None) for h in self.root.handlers]
        self.assertIn(sys.stderr, streams)  # 输出到 stderr
        self.assertEqual(self.root.level, logging.DEBUG)

    def test_setup_logging_keeps_existing_handlers(self):
        self.root.handlers.clear()
        existing = logging.NullHandler()
        self.root.addHandler(existing)

        setup_logging("INFO")

        # 宿主已有 handler 时不清空、也不重复添加
        self.assertEqual(self.root.handlers, [existing])

    def test_get_logger_does_not_configure(self):
        self.root.handlers.clear()
        get_logger("some.module")
        self.assertEqual(self.root.handlers, [])


if __name__ == "__main__":
    unittest.main()
