"""交叉校验（反幻觉）模块测试。"""
import os
import tempfile
import unittest
from types import SimpleNamespace

from src.data.data_service import DataService
from src.verify.facts import FactBase
from src.verify.verifier import CrossVerifier, VerificationReport


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

    # ---- 修复 #10：同比方向必须保留 --------------------------------
    def test_yoy_direction_preserved(self):
        # 事实是 +12.5%，文本说下降/下滑/负号 → 必须解析成负值并判 mismatch
        for text, expect in (
            ("公司营业总收入同比下降12.5%，经营承压。", -12.5),
            ("公司营业总收入同比下滑8.5%。", -8.5),
            ("公司营业总收入同比 -8.5%。", -8.5),
        ):
            report = self.verifier.verify(text, self.fact_base)
            yoy = [c for c in report.claims if c["metric"] == "revenue_yoy"]
            self.assertEqual(len(yoy), 1, text)
            self.assertAlmostEqual(yoy[0]["claimed"], expect, msg=text)
            self.assertEqual(yoy[0]["status"], "mismatch", text)
            self.assertFalse(report.passed, text)

    def test_yoy_positive_still_supported(self):
        report = self.verifier.verify("营业总收入同比增长12.5%。", self.fact_base)
        yoy = next(c for c in report.claims if c["metric"] == "revenue_yoy")
        self.assertAlmostEqual(yoy["claimed"], 12.5)
        self.assertEqual(yoy["status"], "supported")

    def test_yoy_supported_for_genuinely_declining_company(self):
        # 对真实下滑的公司，文本写“同比下降8.5%”应判一致，写“增长8.5%”才判矛盾
        from src.verify.facts import Fact

        class _FakeFactBase:
            def get(self, key):
                if key == "revenue_yoy":
                    return Fact("revenue_yoy", "营业总收入同比增速", -8.5, "percent")
                return None

        fb = _FakeFactBase()
        ok = self.verifier.verify("营业总收入同比下降8.5%。", fb)
        self.assertEqual(next(c for c in ok.claims if c["metric"] == "revenue_yoy")["status"],
                         "supported")
        bad = self.verifier.verify("营业总收入同比增长8.5%。", fb)
        self.assertEqual(next(c for c in bad.claims if c["metric"] == "revenue_yoy")["status"],
                         "mismatch")

    # ---- 修复 #11：同比不得跨指标取数 ------------------------------
    def test_yoy_does_not_borrow_gross_margin(self):
        text = "营业总收入100.5亿元，销售毛利率42.3%，营业总收入同比增长12.5%"
        report = self.verifier.verify(text, self.fact_base)
        yoy = [c for c in report.claims if c["metric"] == "revenue_yoy"]
        self.assertEqual(len(yoy), 1)
        self.assertAlmostEqual(yoy[0]["claimed"], 12.5)
        self.assertEqual(yoy[0]["status"], "supported")

    def test_net_margin_is_not_net_profit_yoy(self):
        # “净利率”不得被当作“净利同比”的锚点
        text = "销售净利率20.88%，净利润同比增长9.4%"
        report = self.verifier.verify(text, self.fact_base)
        yoy = next(c for c in report.claims if c["metric"] == "net_profit_yoy")
        self.assertAlmostEqual(yoy["claimed"], 9.4)

    def test_net_margin_not_reported_as_net_profit_yoy(self):
        report = self.verifier.verify("销售净利率20.88%。", self.fact_base)
        self.assertFalse([c for c in report.claims if c["metric"] == "net_profit_yoy"])

    # ---- 修复 #12：收集全部出现位置 --------------------------------
    def test_all_occurrences_collected(self):
        text = ("前文：营业总收入152.30亿元，同比增长12.50%。"
                "后文又说营业总收入实际为999.00亿元。")
        report = self.verifier.verify(text, self.fact_base)
        revenues = [c for c in report.claims if c["metric"] == "revenue"]
        self.assertEqual(len(revenues), 2)
        self.assertIn(999.0 * 1e8, [c["claimed"] for c in revenues])
        self.assertEqual(sum(1 for c in revenues if c["status"] == "mismatch"), 1)
        self.assertFalse(report.passed)

    def test_hallucination_rate_denominator_is_all_occurrences(self):
        text = ("营业总收入182.30亿元，同比增长12.50%；营业总收入999.00亿元。")
        report = self.verifier.verify(text, self.fact_base)
        self.assertEqual(len(report.claims), 3)          # 182.3 / 12.5 / 999
        self.assertAlmostEqual(report.hallucination_rate, 2 / 3, places=3)

    # ---- 修复 #13：千分位 ------------------------------------------
    def test_thousand_separator(self):
        value, raw, _ = CrossVerifier._read_value("1,234.56亿元", "amount")
        self.assertAlmostEqual(value, 1234.56 * 1e8)
        self.assertIn(",", raw)

        value2, _, _ = CrossVerifier._read_value("1,000万元", "amount")
        self.assertAlmostEqual(value2, 1000 * 1e4)

    def test_thousand_separator_in_text(self):
        # 1,523.00 亿元 ≠ 事实 152.30 亿元 → 必须发现矛盾（旧代码会少一位）
        report = self.verifier.verify("营业总收入1,523.00亿元。", self.fact_base)
        revenue = next(c for c in report.claims if c["metric"] == "revenue")
        self.assertAlmostEqual(revenue["claimed"], 1523.0 * 1e8)
        self.assertEqual(revenue["status"], "mismatch")

    # ---- 修复 #14：容差与浮点边界 -----------------------------------
    def test_relative_tolerance_one_percent(self):
        ok = self.verifier.verify("营业总收入153.50亿元。", self.fact_base)
        self.assertEqual(next(c for c in ok.claims if c["metric"] == "revenue")["status"],
                         "supported")
        bad = self.verifier.verify("营业总收入154.00亿元。", self.fact_base)
        self.assertEqual(next(c for c in bad.claims if c["metric"] == "revenue")["status"],
                         "mismatch")

    def test_pct_tolerance_and_float_boundary(self):
        # 12.6 - 12.5 在二进制下是 0.09999999999999964，必须仍判一致
        ok = self.verifier.verify("营业总收入同比增长12.6%。", self.fact_base)
        self.assertEqual(next(c for c in ok.claims if c["metric"] == "revenue_yoy")["status"],
                         "supported")
        bad = self.verifier.verify("营业总收入同比增长12.8%。", self.fact_base)
        self.assertEqual(next(c for c in bad.claims if c["metric"] == "revenue_yoy")["status"],
                         "mismatch")

    # ---- 修复 #7：price 类抽取约束 ----------------------------------
    def test_price_not_captured_from_percent(self):
        report = self.verifier.verify("股价较年初上涨20.3%。", self.fact_base)
        self.assertFalse([c for c in report.claims if c["metric"] == "latest_close"])

    def test_pe_not_captured_from_share_price(self):
        report = self.verifier.verify("PE为负，股价12.5元。", self.fact_base)
        close = [c for c in report.claims if c["metric"] == "latest_close"]
        self.assertEqual(len(close), 1)
        self.assertAlmostEqual(close[0]["claimed"], 12.5)
        # 不得把 12.5 当成 PE
        self.assertFalse([c for c in report.claims
                          if c["metric"] == "pe_ratio" and c["claimed"] == 12.5])

    def test_price_requires_unit_for_index_metrics(self):
        report = self.verifier.verify("最新收盘价26.10元。", self.fact_base)
        close = next(c for c in report.claims if c["metric"] == "latest_close")
        self.assertAlmostEqual(close["claimed"], 26.10)
        self.assertEqual(close["status"], "supported")
        # 没写“元”时不应把裸数字当股价
        report2 = self.verifier.verify("最新收盘价26.10。", self.fact_base)
        self.assertFalse([c for c in report2.claims if c["metric"] == "latest_close"])

    # ---- 修复 #6：unverifiable 不得抬高可信度 ------------------------
    def test_unverifiable_downgrades_confidence(self):
        def claim(status):
            return {"metric": "x", "label": "x", "claimed_raw": "", "status": status}

        mostly_unverified = VerificationReport(
            claims=[claim("supported")] * 4 + [claim("unverifiable")] * 6
        )
        self.assertEqual(mostly_unverified.hallucination_rate, 0.0)
        self.assertFalse(mostly_unverified.confidence_level.startswith("A"))

        all_unverified = VerificationReport(claims=[claim("unverifiable")] * 10)
        self.assertFalse(all_unverified.confidence_level.startswith("A"))

        clean = VerificationReport(claims=[claim("supported")] * 10)
        self.assertTrue(clean.confidence_level.startswith("A"))


class StatementCheckTest(unittest.TestCase):
    def test_empty_input_is_not_fail_open(self):
        from src.verify.statements import check_statements

        report = check_statements({}, {}, {})
        self.assertFalse(report.passed)
        self.assertFalse(report.data_sufficient)
        self.assertEqual(report.to_dict()["status"], "insufficient")
        self.assertGreater(report.n_skipped, 0)
        self.assertIn("数据不足", report.render_text())

    def test_full_valid_statements_pass(self):
        from src.verify.statements import check_statements

        balance = {"total_assets": 1000.0, "total_liabilities": 400.0, "total_equity": 600.0}
        income = {"revenue": 500.0, "operating_cost": 300.0, "operating_profit": 150.0,
                  "total_profit": 120.0, "income_tax": 20.0, "net_profit": 100.0}
        cashflow = {"begin_cash": 100.0, "end_cash": 180.0,
                    "net_op_cash": 90.0, "net_inv_cash": -20.0, "net_fin_cash": 10.0}
        report = check_statements(balance, income, cashflow)
        self.assertTrue(report.data_sufficient)
        self.assertTrue(report.passed)
        self.assertEqual(report.n_errors, 0)

    def test_attributable_net_profit_not_mixed_with_total(self):
        from src.verify.statements import check_statements

        # 只有归母净利润、没有合并净利润时，净利润=利润总额-所得税 这条不能执行
        income = {"total_profit": 100.0, "income_tax": 15.0, "net_profit_attr": 85.0}
        report = check_statements(income=income)
        item = next(i for i in report.items if i.name == "利润表净利润勾稽")
        self.assertFalse(item.checked)

    def test_net_profit_identity_uses_total_scope(self):
        from src.verify.statements import check_statements

        income = {"total_profit": 100.0, "income_tax": 15.0, "net_profit": 85.0}
        report = check_statements(income=income)
        item = next(i for i in report.items if i.name == "利润表净利润勾稽")
        self.assertTrue(item.checked)
        self.assertTrue(item.passed)

    def test_statements_from_row_keeps_two_scopes_separate(self):
        from src.verify.statements import statements_from_row

        out = statements_from_row({"净利润": 85.0, "归母净利润": 80.0})
        self.assertEqual(out["income"]["net_profit"], 85.0)
        self.assertEqual(out["income"]["net_profit_attr"], 80.0)


class SemanticCheckerTest(unittest.TestCase):
    class _FakeLLM:
        offline = False

        def __init__(self, data):
            self._data = data

        def chat_json(self, messages, **kwargs):
            if isinstance(self._data, Exception):
                raise self._data
            return self._data

    def test_threshold_is_used(self):
        from src.verify.semantic_check import SemanticChecker

        data = {"claims": [
            {"claim": "a", "supported": True},
            {"claim": "b", "supported": True},
            {"claim": "c", "supported": False, "reason": "无依据"},
        ]}
        report = SemanticChecker(self._FakeLLM(data), threshold=0.5).check("t", "f")
        self.assertTrue(report.passed)
        self.assertAlmostEqual(report.faithfulness_score, 0.667)

        strict = SemanticChecker(self._FakeLLM(data), threshold=0.7).check("t", "f")
        self.assertFalse(strict.passed)

    def test_offline_is_not_a_pass(self):
        from src.verify.semantic_check import SemanticChecker

        class _Offline:
            offline = True

        report = SemanticChecker(_Offline()).check("t", "f")
        self.assertFalse(report.passed)
        self.assertFalse(report.checked)
        self.assertEqual(report.status, "skipped")
        self.assertEqual(report.faithfulness_score, 0.0)

    def test_exception_is_not_a_pass(self):
        from src.verify.semantic_check import SemanticChecker

        report = SemanticChecker(self._FakeLLM(RuntimeError("boom"))).check("t", "f")
        self.assertFalse(report.passed)
        self.assertEqual(report.status, "failed")

    def test_empty_or_malformed_claims_are_not_a_pass(self):
        from src.verify.semantic_check import SemanticChecker

        empty = SemanticChecker(self._FakeLLM({"claims": []})).check("t", "f")
        self.assertFalse(empty.passed)
        self.assertEqual(empty.status, "empty")
        self.assertEqual(empty.faithfulness_score, 0.0)

        malformed = SemanticChecker(self._FakeLLM("not a dict")).check("t", "f")
        self.assertFalse(malformed.passed)
        self.assertEqual(malformed.status, "failed")


class StatsTest(unittest.TestCase):
    def test_render_and_weighted_average(self):
        from src.verify.stats import HallucinationStats

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".stats.json")
            stats = HallucinationStats(path)
            self.assertIn("暂无", stats.render_text())

            stats.record("主题A", "模型A", total_claims=10, mismatch=1,
                         hallucination_rate=0.1, confidence_level="B（中可信）")
            stats.record("主题B", "模型B", total_claims=100, mismatch=5,
                         hallucination_rate=0.05, confidence_level="B（中可信）")

            text = stats.render_text()
            self.assertNotIn("暂无", text)
            self.assertIn("模型A", text)

            md = stats.render_markdown()
            self.assertIn("# 📊", md)
            self.assertIn("|", md)
            self.assertIn("模型B", md)

            summary = stats.summary()
            self.assertEqual(summary["total_runs"], 2)
            # 加权：(1+5)/(10+100)=6/110，而不是 (0.1+0.05)/2
            self.assertAlmostEqual(summary["avg_hallucination_rate"], round(6 / 110, 4),
                                   places=4)


class FactsRenderingTest(unittest.TestCase):
    def test_missing_amounts_render_as_dash(self):
        bundle = SimpleNamespace(
            name="x", symbol="000000", as_of="", source="sample",
            financial_summary={
                "latest": {}, "latest_period": "2025-12-31",
                "recent_periods": [
                    {"period": "2025-12-31", "revenue_yuan": None,
                     "net_profit_yuan": 1.5e8},
                ],
            },
            price_summary={}, valuation={}, fund_flow={}, news=[],
        )
        table = FactBase(bundle).recent_periods_table()
        self.assertNotIn("0.00", table)
        self.assertIn("—", table)
        self.assertIn("1.50", table)


if __name__ == "__main__":
    unittest.main()
