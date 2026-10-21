"""交叉校验：表格归因 / 分期矛盾 / 显示精度容差（全离线）。

这几条都是真实报告里踩出来的坑：

1. 「近五期趋势」表格里没有任何一处把指标名写在数字旁边，
   纯文本正则在结构上就抽不到 → 整张表被判「无来源」；
2. 单位写在表格上方的标题行（``**近五期趋势（单位：亿元）**``），
   表头只有「营业总收入」→ 13.64 会被当成 13.64 元，整张表判「矛盾」；
3. 同一指标不同报告期的值本来就不该相等，笼统归组会误报「自相矛盾」；
4. 只写了两位小数的正确数字（0.09 亿 vs 0.091071 亿）会被 0.5% 容差判「矛盾」。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.verify.facts import FactBase
from src.verify.verifier import CrossVerifier

PERIODS = [
    {
        "period": "2026-09-30",
        "revenue_yuan": 1_364_000_000,
        "net_profit_yuan": 176_000_000,
        "revenue_yoy_pct": 70.71,
        "net_profit_yoy_pct": 869.79,
        "gross_margin_pct": 29.99,
        "net_margin_pct": 13.60,
        "roe_pct": 11.38,
        "eps": 0.269,
    },
    {
        "period": "2025-12-31",
        "revenue_yuan": 1_066_000_000,
        "net_profit_yuan": 20_146_000,
        "revenue_yoy_pct": 38.89,
        "net_profit_yoy_pct": -62.06,
        "gross_margin_pct": 21.12,
        "net_margin_pct": 5.0,
        "roe_pct": 1.37,
        "eps": 0.03,
    },
    {
        "period": "2026-03-31",
        "revenue_yuan": 289_000_000,
        "net_profit_yuan": 9_107_100,
        "revenue_yoy_pct": 20.31,
        "net_profit_yoy_pct": -10.71,
        "gross_margin_pct": 3.70,
        "net_margin_pct": 3.15,
        "roe_pct": 0.62,
        "eps": 0.014,
    },
]


def _bundle(periods=None) -> SimpleNamespace:
    periods = periods if periods is not None else PERIODS
    latest = periods[0]
    return SimpleNamespace(
        source="test",
        financial_summary={
            "latest_period": latest["period"],
            "latest": {
                "revenue_yuan": latest["revenue_yuan"],
                "net_profit_yuan": latest["net_profit_yuan"],
                "revenue_yoy_pct": latest["revenue_yoy_pct"],
                "net_profit_yoy_pct": latest["net_profit_yoy_pct"],
                "gross_margin_pct": latest["gross_margin_pct"],
                "net_margin_pct": latest["net_margin_pct"],
                "roe_pct": latest["roe_pct"],
                "eps": latest["eps"],
            },
            "recent_periods": periods,
        },
        price_summary={},
        valuation={},
        fund_flow={},
    )


class TableAttributionTest(unittest.TestCase):
    def setUp(self):
        self.verifier = CrossVerifier()
        self.fact_base = FactBase(_bundle())

    TABLE = (
        "**近五期趋势（单位：亿元）**\n"
        "| 报告期 | 营业总收入 | 归母净利润 | 营收同比 | 净利同比 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 2026-09-30 | 13.64 | 1.76 | +70.71% | +869.79% |\n"
        "| 2025-12-31 | 10.66 | 0.20 | +38.89% | -62.06% |\n"
    )

    def test_table_cells_become_claims(self):
        report = self.verifier.verify(self.TABLE, self.fact_base)
        metrics = {c["metric"] for c in report.claims}
        self.assertTrue({"revenue", "net_profit", "revenue_yoy", "net_profit_yoy"} <= metrics)

    def test_block_unit_from_title_line_is_applied(self):
        report = self.verifier.verify(self.TABLE, self.fact_base)
        revenue = [c for c in report.claims if c["metric"] == "revenue"]
        self.assertTrue(revenue)
        # 13.64 亿 必须换算成 1.364e9，而不是 13.64
        self.assertAlmostEqual(max(c["claimed"] for c in revenue), 1.364e9, delta=1)

    def test_table_numbers_are_supported_not_unsourced(self):
        report = self.verifier.verify(self.TABLE, self.fact_base)
        self.assertEqual(report.n_mismatch, 0)
        self.assertEqual(len(report.unsourced), 0)
        self.assertGreaterEqual(report.n_supported, 8)
        self.assertEqual(report.coverage, 1.0)

    def test_two_precision_values_match_period(self):
        report = self.verifier.verify(self.TABLE, self.fact_base)
        periods = {c.get("period") for c in report.claims if c["metric"] == "revenue"}
        self.assertIn("2026-09-30", periods)
        self.assertIn("2025-12-31", periods)


class ContradictionPeriodTest(unittest.TestCase):
    def setUp(self):
        self.verifier = CrossVerifier()
        self.fact_base = FactBase(_bundle())

    def test_cross_period_values_are_not_contradictions(self):
        """2026Q3 的 +869.79% 与 2025 年报的 -62.06% 本来就不同，不能算打架。"""
        table = (
            "| 报告期 | 营业总收入 | 净利同比 |\n"
            "| --- | --- | --- |\n"
            "| 2026-09-30 | 13.64 | +869.79% |\n"
            "| 2025-12-31 | 10.66 | -62.06% |\n"
        )
        report = self.verifier.verify(table, self.fact_base)
        self.assertEqual(report.contradictions, [])

    def test_same_period_conflict_is_reported(self):
        table = (
            "| 报告期 | 营业总收入 |\n"
            "| --- | --- |\n"
            "| 2026-09-30 | 13.64 |\n"
            "| 2026-09-30 | 15.00 |\n"
        )
        report = self.verifier.verify(table, self.fact_base)
        self.assertEqual(len(report.contradictions), 1)
        self.assertIn("2026-09-30", report.contradictions[0]["note"])


class DisplayPrecisionTest(unittest.TestCase):
    def setUp(self):
        self.verifier = CrossVerifier()
        self.fact_base = FactBase(_bundle())

    def test_two_decimal_small_amount_is_not_a_mismatch(self):
        """0.09 亿 = 9.0e6，真实值 9,107,100（相差 1.2%）。

        写出来的精度只有 ±0.005 亿（±5e5），按真实值的 0.5% 相对容差会误判；
        按显示精度放宽后必须判为一致。
        """
        table = (
            "**单位：亿元**\n"
            "| 报告期 | 归母净利润 |\n"
            "| --- | --- |\n"
            "| 2026-03-31 | 0.09 |\n"
        )
        report = self.verifier.verify(table, self.fact_base)
        net = [c for c in report.claims if c["metric"] == "net_profit"]
        self.assertEqual(net[0]["status"], "supported", net[0]["note"])

    def test_clearly_wrong_amount_still_fails(self):
        table = (
            "**单位：亿元**\n"
            "| 报告期 | 归母净利润 |\n"
            "| --- | --- |\n"
            "| 2026-03-31 | 0.50 |\n"
        )
        report = self.verifier.verify(table, self.fact_base)
        net = [c for c in report.claims if c["metric"] == "net_profit"]
        self.assertEqual(net[0]["status"], "mismatch")


class ImplicitSourcedTest(unittest.TestCase):
    def setUp(self):
        self.verifier = CrossVerifier()
        self.fact_base = FactBase(_bundle())

    def test_amount_matching_fact_is_implicit_not_unsourced(self):
        report = self.verifier.verify("本期录得 13.64 亿元。", self.fact_base)
        self.assertEqual(len(report.unsourced), 0)
        self.assertEqual(len(report.implicit), 1)
        self.assertIn("营业总收入", report.implicit[0]["hint"])

    def test_percent_matching_fact_stays_unsourced(self):
        """百分比不放宽：3.70% 这种两位小数太容易撞上别的指标。"""
        report = self.verifier.verify("市占率约为 3.70%。", self.fact_base)
        self.assertEqual(len(report.implicit), 0)
        self.assertEqual(len(report.unsourced), 1)

    def test_unknown_amount_stays_unsourced(self):
        report = self.verifier.verify("研发投入 8.60 亿元。", self.fact_base)
        self.assertEqual(len(report.implicit), 0)
        self.assertEqual(len(report.unsourced), 1)

    def test_implicit_counts_toward_coverage(self):
        report = self.verifier.verify("本期录得 13.64 亿元。", self.fact_base)
        self.assertEqual(report.coverage, 1.0)
        self.assertEqual(report.unsourced_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
