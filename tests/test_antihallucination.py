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


class ChineseNumeralTest(unittest.TestCase):
    """T1：中文数字此前完全漏检（"一百五十二点三亿元"）。"""

    @classmethod
    def setUpClass(cls):
        cls.fact_base = FactBase(
            DataService().get_company_bundle("000000", prefer_live=False)
        )
        cls.verifier = CrossVerifier()

    def test_cn_conversion(self):
        from src.verify.numbers import cn_to_number
        self.assertAlmostEqual(cn_to_number("一百五十二点三"), 152.3)
        self.assertAlmostEqual(cn_to_number("三万亿"), 3e12)
        self.assertAlmostEqual(cn_to_number("一万二千"), 12000)
        self.assertAlmostEqual(cn_to_number("三点五"), 3.5)

    def test_cn_amount_is_extracted(self):
        mentions = extract_numbers("营业总收入一百五十二点三亿元。")
        self.assertTrue(mentions, "中文数字必须能被抽出来")
        self.assertEqual(mentions[0].kind, KIND_AMOUNT)
        # 浮点会有极小尾差（152.3×1e8），用相对量级比较
        self.assertAlmostEqual(mentions[0].value / 1e8, 152.3, places=6)

    def test_cn_amount_is_attributed_and_supported(self):
        """不只是抽出来，还要能归因到指标并判定一致。"""
        report = self.verifier.verify("营业总收入一百五十二点三亿元。", self.fact_base)
        revenue = [c for c in report.claims if c["metric"] == "revenue"]
        self.assertEqual(len(revenue), 1, report.to_dict())
        self.assertEqual(revenue[0]["status"], "supported")

    def test_cn_percent_and_baifenzhi(self):
        for text in ("销售毛利率四十二点三%。", "销售毛利率百分之四十二点三。"):
            report = self.verifier.verify(text, self.fact_base)
            gm = [c for c in report.claims if c["metric"] == "gross_margin"]
            self.assertEqual(len(gm), 1, text)
            self.assertEqual(gm[0]["status"], "supported", text)

    def test_cn_wrong_value_is_mismatch(self):
        report = self.verifier.verify("营业总收入一百八十二点三亿元。", self.fact_base)
        revenue = [c for c in report.claims if c["metric"] == "revenue"][0]
        self.assertEqual(revenue["status"], "mismatch")

    def test_cn_counter_is_not_money(self):
        """带量词的计数不该被当成金额（否则会污染覆盖率分母）。"""
        mentions = extract_numbers("公司共有一万二千人。")
        self.assertTrue(all(m.kind == KIND_BARE for m in mentions), mentions)


class ThreeStateVerdictTest(unittest.TestCase):
    """T1：结论必须三态——覆盖率不足时不允许显示"通过"。"""

    @classmethod
    def setUpClass(cls):
        cls.fact_base = FactBase(
            DataService().get_company_bundle("000000", prefer_live=False)
        )
        cls.verifier = CrossVerifier()

    def test_clean_report_passes(self):
        report = self.verifier.verify(
            "营业总收入152.30亿元，归母净利润31.80亿元，"
            "销售毛利率42.30%，净资产收益率15.60%。",
            self.fact_base,
        )
        self.assertEqual(report.verdict, "pass")
        self.assertTrue(report.passed)
        self.assertEqual(report.verdict_reasons, [])

    def test_mismatch_fails(self):
        report = self.verifier.verify("营业总收入182.30亿元。", self.fact_base)
        self.assertEqual(report.verdict, "fail")
        self.assertFalse(report.passed)
        self.assertTrue(report.verdict_reasons)

    def test_many_unsourced_is_review_not_pass(self):
        """旧版：无矛盾 -> "通过"。这正是最危险的假阳性安全感。"""
        report = self.verifier.verify(
            "营业总收入152.30亿元，研发投入8.6亿元，市占率3.7%，"
            "产能利用率78%，海外收入占比41%，专利230项正在申请。",
            self.fact_base,
        )
        self.assertFalse(report.has_mismatch)
        self.assertEqual(report.verdict, "review")
        self.assertFalse(report.passed)
        self.assertTrue(any("无来源" in r for r in report.verdict_reasons), report.verdict_reasons)

    def test_no_numbers_is_review(self):
        report = self.verifier.verify("公司经营稳健，行业前景良好。", self.fact_base)
        self.assertEqual(report.verdict, "review")
        self.assertFalse(report.passed)

    def test_verdict_exposed_in_to_dict(self):
        report = self.verifier.verify("营业总收入152.30亿元。", self.fact_base)
        d = report.to_dict()
        for key in ("verdict", "verdict_text", "verdict_reasons", "passed_lenient"):
            self.assertIn(key, d)
        self.assertTrue(d["passed_lenient"])


class RagRelevanceThresholdTest(unittest.TestCase):
    """T1：RAG 必须过滤不相关片段，不能无条件塞进 prompt。"""

    def _store(self):
        from src.rag.vector_store import VectorStore
        store = VectorStore(persist_path="")
        store.add("贵州茅台2025年营业总收入152.30亿元，同比增长12.5%。", source="茅台")
        return store

    def test_irrelevant_query_returns_nothing(self):
        store = self._store()
        # 高阈值下，与库内文档毫不相关的查询不应返回任何片段
        hits = store.search("量子计算 超导 材料 低温 物理", top_k=3, min_score=0.9)
        self.assertEqual(hits, [])

    def test_relevant_query_returns_hit(self):
        store = self._store()
        hits = store.search("贵州茅台 营业总收入", top_k=1, min_score=-1.0)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["source"], "茅台")
        self.assertIn("score", hits[0])


if __name__ == "__main__":
    unittest.main()
