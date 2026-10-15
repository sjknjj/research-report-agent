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
        # 离线模式必须直接跳过三表勾稽：不发网络请求，也不伪造结果
        self.assertTrue(result.extra["statements_skipped"])
        self.assertIsNone(result.extra["statements"])
        self.assertIsNone(result.extra["statements_error"])

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

    # -- 第 12 条：新浪财报接口需要交易所前缀 -----------------------------
    def test_sina_symbol_prefix(self):
        conv = ResearchAssistant._to_sina_symbol
        self.assertEqual(conv("600519"), "sh600519")
        self.assertEqual(conv("000858"), "sz000858")
        self.assertEqual(conv("300750"), "sz300750")
        self.assertEqual(conv("830799"), "bj830799")
        self.assertEqual(conv("sh600519"), "sh600519")  # 已带前缀保持原样

    # -- 第 12 条：可选校验失败不得拖垮公告摘要 ---------------------------
    def test_announcement_survives_cross_check_failure(self):
        settings = load_settings(cli_overrides={"mode": "offline"})
        assistant = ResearchAssistant(settings)

        def boom(*args, **kwargs):
            raise RuntimeError("数据层不可用")

        assistant.data.get_company_bundle = boom
        result = assistant.extract_announcement(
            sample_data.sample_announcement_text(),
            title="业绩快报",
            symbol="000000",
            prefer_live=False,
        )
        self.assertTrue(result.text)          # 公告摘要仍然成功
        self.assertIsNone(result.verification)  # 可选校验被跳过


if __name__ == "__main__":
    unittest.main()
