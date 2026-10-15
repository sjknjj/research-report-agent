"""离线模式下端到端流水线测试。"""
import unittest

from src.agent.orchestrator import ResearchOrchestrator
from src.common.config import load_settings
from src.export.exporter import build_html, build_markdown


class OfflinePipelineTest(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings(
            cli_overrides={"mode": "offline", "search_provider": "mock"}
        )
        self.topic = "低空经济产业链研究"

    def test_end_to_end(self):
        report = ResearchOrchestrator(self.settings).run(self.topic)

        # 结构完整性
        self.assertEqual(report.topic, self.topic)
        self.assertGreaterEqual(len(report.sections), 5)
        self.assertTrue(report.abstract.strip())
        self.assertGreater(len(report.references), 0)

        # Markdown 渲染
        md = build_markdown(report)
        self.assertIn(self.topic, md)
        self.assertIn("摘要", md)
        self.assertIn("参考文献", md)
        self.assertIn("离线演示模式", md)  # 离线必须有显著提示
        for section in report.sections:
            self.assertIn(section.heading, md)

        # HTML 渲染为自包含页面
        html = build_html(report)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<h1", html)
        self.assertIn(self.topic, html)

    def test_review_loop_runs(self):
        self.settings.max_review_rounds = 1
        report = ResearchOrchestrator(self.settings).run(self.topic)
        self.assertTrue(report.meta.get("trace"))


if __name__ == "__main__":
    unittest.main()
