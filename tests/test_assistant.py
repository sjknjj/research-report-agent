"""投研助手离线端到端测试。"""
import unittest

from src.common.config import load_settings
from src.data import sample_data
from src.services.research_assistant import ResearchAssistant


class ResearchAssistantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = load_settings(
            cli_overrides={"mode": "offline", "max_review_rounds": 1}
        )
        cls.assistant = ResearchAssistant(settings)

    def test_company_brief(self):
        result = self.assistant.company_brief("000000", prefer_live=False)
        self.assertTrue(result.text)
        self.assertIsNotNone(result.verification)
        # 离线模板数字全部来自事实包，交叉校验应零矛盾
        self.assertTrue(result.verification["passed"])
        self.assertEqual(result.verification["mismatch"], 0)
        self.assertEqual(result.bundle["source"], "sample")

    def test_announcement_cross_check(self):
        result = self.assistant.extract_announcement(
            sample_data.sample_announcement_text(),
            title="业绩快报",
            symbol="000000",
            prefer_live=False,
        )
        self.assertTrue(result.text)
        self.assertIsNotNone(result.verification)
        self.assertTrue(result.verification["passed"])

    def test_industry(self):
        result = self.assistant.summarize_industry("演示行业")
        self.assertTrue(result.text)
        self.assertIn("count", result.extra["stats"])


if __name__ == "__main__":
    unittest.main()
