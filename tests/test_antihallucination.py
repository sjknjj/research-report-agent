"""反幻觉升级（U1–U5）的回归测试。

覆盖：
- U1 全量数字抽取 + 无来源归因 + 覆盖率
- U2 环比事实与分期归因
- U3 报告内部自相矛盾检测
- U4 可信度按覆盖率/无来源占比校准
- U5 引用溯源校验（数字必须能在引用来源中找到）
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.data_service import DataService  # noqa: E402
from src.verify.citation import check_citations  # noqa: E402
from src.verify.facts import FactBase  # noqa: E402
from src.verify.numbers import (  # noqa: E402
    KIND_AMOUNT,
    KIND_BARE,
    KIND_PERCENT,
    extract_numbers,
)
from src.verify.verifier import CrossVerifier  # noqa: E402


class NumberExtractionTest(unittest.TestCase):
    """U1：与词典无关的全量抽取。"""

    def test_amounts_normalised_to_yuan(self):
        got = {m.literal: m.value for m in extract_numbers("营收1,234.56亿元，另5000万元")}
        self.assertAlmostEqual(got["1,234.56"], 1234.56e8)
        self.assertAlmostEqual(got["5000"], 5000e4)

    def test_units_classified(self):
        kinds = {m.raw: m.kind for m in extract_numbers("营收152.3亿元，毛利率42.3%，PE 25倍，股价12.5元")}
        self.assertEqual(kinds["152.3亿元"], KIND_AMOUNT)
        self.assertEqual(kinds["42.3%"], KIND_PERCENT)

    def test_years_and_codes_are_noise(self):
        mentions = extract_numbers("2025年公司代码600519，员工100人")
        bare = [m for m in mentions if m.kind == KIND_BARE]
        self.assertEqual(len(bare), len(mentions), "年份/代码/计数都应归为 bare 噪声")


class UnattributedNumbersTest(unittest.TestCase):
    """U1 + U4：词典外的数字不能再被静默放过。"""

    @classmethod
    def setUpClass(cls):
        cls.fact_base = FactBase(
            DataService().get_company_bundle("000000", prefer_live=False)
        )
        cls.verifier = CrossVerifier()

    def test_off_dictionary_numbers_are_flagged(self):
        text = "营业总收入152.30亿元，研发投入8.60亿元，市占率3.70%。"
        report = self.verifier.verify(text, self.fact_base)
        raws = [u["raw"] for u in report.unsourced]
        self.assertTrue(any("8.60" in r for r in raws), raws)
        self.assertTrue(any("3.70" in r for r in raws), raws)
        # 词典命中的营收不应出现在无来源清单里
        self.assertFalse(any("152.30" in r for r in raws), raws)

    def test_coverage_and_downgrade(self):
        clean = self.verifier.verify(
            "营业总收入152.30亿元，归母净利润31.80亿元，销售毛利率42.30%，"
            "净资产收益率15.60%，营业总收入同比增长12.50%。",
            self.fact_base,
        )
        noisy = self.verifier.verify(
            "营业总收入152.30亿元，研发投入8.6亿元，市占率3.7%，产能利用率78%，"
            "海外收入占比41%，专利230项正在申请中。",
            self.fact_base,
        )
        self.assertGreater(clean.coverage, noisy.coverage)
        self.assertFalse(noisy.confidence_level.startswith("A"))
        self.assertTrue(noisy.confidence_reasons)

    def test_total_numbers_counts_both(self):
        report = self.verifier.verify("营业总收入152.30亿元，研发投入8.60亿元。",
                                      self.fact_base)
        self.assertEqual(report.total_numbers, len(report.claims) + len(report.unsourced))
        self.assertGreaterEqual(report.total_numbers, 2)


class ContradictionTest(unittest.TestCase):
    """U3：同一指标文中前后不一致。"""

    def test_same_metric_two_values(self):
        fb = FactBase(DataService().get_company_bundle("000000", prefer_live=False))
        report = CrossVerifier().verify(
            "营业总收入152.30亿元。……后文又称营业总收入182.30亿元。", fb
        )
        self.assertEqual(len(report.contradictions), 1)
        self.assertIn("不一致", report.contradictions[0]["note"])

    def test_consistent_repeats_are_not_contradictions(self):
        fb = FactBase(DataService().get_company_bundle("000000", prefer_live=False))
        report = CrossVerifier().verify(
            "营业总收入152.30亿元，前文亦为152.30亿元。", fb
        )
        self.assertEqual(report.contradictions, [])


class PeriodFactTest(unittest.TestCase):
    """U2：环比事实与分期索引。"""

    @classmethod
    def setUpClass(cls):
        cls.fact_base = FactBase(
            DataService().get_company_bundle("000000", prefer_live=False)
        )

    def test_qoq_fact_exists(self):
        fact = self.fact_base.get("revenue_qoq")
        self.assertIsNotNone(fact)
        self.assertIsNotNone(fact.value)

    def test_period_facts_indexed(self):
        self.assertTrue(self.fact_base.known_periods())
        periods = self.fact_base.known_periods()
        self.assertIn("revenue", self.fact_base.facts_by_period[periods[0]])

    def test_get_for_period_falls_back(self):
        fact = self.fact_base.get_for_period("revenue", "1999")
        self.assertIsNotNone(fact)   # 未知期 -> 回退最新


class CitationGroundingTest(unittest.TestCase):
    """U5：深度研报的引用溯源。"""

    SOURCES = [
        {"index": 1, "title": "政策解读",
         "content": "到2027年，低空经济市场规模有望达到1.5万亿元，年均增速约25%。"},
        {"index": 2, "title": "协会数据",
         "content": "2025年无人机注册数量达到220万架。"},
    ]

    def test_grounded_numbers_pass(self):
        text = "市场规模有望达到1.5万亿元[1]，年均增速约25%[1]。"
        rep = check_citations(text, self.SOURCES)
        self.assertTrue(rep.checked)
        self.assertEqual(rep.ungrounded, [])
        self.assertEqual(rep.coverage, 1.0)

    def test_fabricated_number_is_caught(self):
        text = "另有机构预测2030年市场规模将达到9.9万亿元[1]。"
        rep = check_citations(text, self.SOURCES)
        self.assertTrue(rep.ungrounded)
        self.assertEqual(rep.grounded, 0)

    def test_uncited_number_counted(self):
        text = "行业整体规模约3.3万亿元。"      # 没有 [n]
        rep = check_citations(text, self.SOURCES)
        self.assertEqual(rep.uncited, 1)
        self.assertEqual(rep.grounded, 0)

    def test_no_body_is_not_checked(self):
        text = "规模1.5万亿元[1]。"
        rep = check_citations(text, [{"index": 1, "title": "只有标题", "content": ""}])
        self.assertFalse(rep.checked)
        self.assertIn("未执行", rep.render_text())

    def test_empty_input(self):
        rep = check_citations("", self.SOURCES)
        self.assertFalse(rep.checked)
        self.assertEqual(rep.total_numbers, 0)


if __name__ == "__main__":
    unittest.main()
