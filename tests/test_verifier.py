"""交叉校验（反幻觉）模块测试。"""
import unittest

from src.data.data_service import DataService
from src.verify.facts import FactBase
from src.verify.verifier import CrossVerifier


class CrossVerifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = DataService().get_company_bundle("000000", prefer_live=False)
        cls.fact_base = FactBase(cls.bundle)
        cls.verifier = CrossVerifier()

    def test_correct_draft_passes(self):
        good = ("营业总收入152.30亿元，同比增长12.50%；归母净利润31.80亿元，"
                "同比增长9.40%；销售毛利率42.30%，净资产收益率15.60%，基本每股收益1.27元。")
        report = self.verifier.verify(good, self.fact_base)
        self.assertTrue(report.passed)
        self.assertEqual(report.n_mismatch, 0)
        self.assertGreaterEqual(report.n_supported, 6)

    def test_hallucinated_numbers_caught(self):
        bad = ("营业总收入182.30亿元，同比增长12.50%；归母净利润31.80亿元，"
               "同比增长15.20%；销售毛利率50.00%，净资产收益率15.60%。")
        report = self.verifier.verify(bad, self.fact_base)
        self.assertFalse(report.passed)
        self.assertEqual(report.n_mismatch, 3)
        self.assertGreater(report.hallucination_rate, 0)
        # 纠错清单必须给出正确值
        joined = " ".join(report.corrections)
        self.assertIn("152.30", joined)
        self.assertIn("42.30", joined)

    def test_negative_percent(self):
        # 区间涨跌幅为 -7.35%，负号必须被正确捕获
        text = "该股票区间涨跌幅为-7.35%。"
        report = self.verifier.verify(text, self.fact_base)
        target = next(c for c in report.claims if c["metric"] == "period_return")
        self.assertAlmostEqual(target["claimed"], -7.35)
        self.assertEqual(target["status"], "supported")


if __name__ == "__main__":
    unittest.main()
