"""三大报表字段映射健壮化（F2）与离线行业样例数据驱动（F3）的单元测试。

覆盖：
- ``clean_number`` 数值清洗（缺失占位符、千分位、万/亿单位、全角负号，且绝不抛异常）；
- ``match_columns`` 列名匹配（精确 → 归一化 → 包含，一列只归属一个科目）；
- ``normalize_statement_row`` 命中/缺失/不可解析科目诊断；
- ``_parse_statement_frames`` 三表字段级诊断与缺科目提示；
- ``data/samples/sample_industry_raw.csv`` 的数据驱动统计与生成器一致性。
"""
import importlib.util
import math
import unittest
from pathlib import Path

import pandas as pd

from src.services.research_assistant import ResearchAssistant
from src.verify.statements import (
    TABLE_ALIASES,
    clean_number,
    match_columns,
    normalize_statement_row,
    statements_from_row,
)

_ROOT = Path(__file__).resolve().parents[1]
_SAMPLE_CSV = _ROOT / "data" / "samples" / "sample_industry_raw.csv"
_GEN_PATH = _ROOT / "scripts" / "generate_samples.py"


def _load_generator():
    """按文件路径加载生成脚本（scripts 不是包）。"""
    spec = importlib.util.spec_from_file_location("_gen_samples_for_test", _GEN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CleanNumberTest(unittest.TestCase):
    """数值清洗：脏数据要安全转换或明确判缺失，绝不抛异常。"""

    def test_missing_placeholders(self):
        for value in (None, "", "   ", "--", "-", "—", "/", "N/A", "nan",
                      "None", "无", float("nan"), math.nan, True, False):
            self.assertIsNone(clean_number(value), f"{value!r} 应判为缺失")

    def test_plain_and_thousands(self):
        self.assertEqual(clean_number(1234), 1234.0)
        self.assertEqual(clean_number(-5), -5.0)
        self.assertEqual(clean_number(1234.56), 1234.56)
        self.assertEqual(clean_number("1234.56"), 1234.56)
        self.assertEqual(clean_number("1,234.56"), 1234.56)
        self.assertEqual(clean_number(" 1,234.56 "), 1234.56)

    def test_unit_suffix_converted_to_yuan(self):
        # 带“万/亿”单位时统一折算为“元”，保证同一张表内口径一致
        self.assertEqual(clean_number("1234.56万元"), 1234.56 * 1e4)
        self.assertEqual(clean_number("1.5亿"), 1.5 * 1e8)
        self.assertEqual(clean_number("2亿元"), 2e8)
        self.assertEqual(clean_number("1万亿"), 1e12)
        self.assertEqual(clean_number("300元"), 300.0)

    def test_fullwidth_minus_and_symbols(self):
        self.assertEqual(clean_number("－1,234.5"), -1234.5)
        self.assertEqual(clean_number("−12"), -12.0)
        self.assertEqual(clean_number("¥1,000"), 1000.0)

    def test_never_raises_on_garbage(self):
        for value in ("abc", "元", "--万元", "¥", "12abc", "约100", object()):
            try:
                clean_number(value)
            except Exception as exc:  # pragma: no cover - 失败时给出明确信息
                self.fail(f"clean_number({value!r}) 不应抛异常：{exc}")


class MatchColumnsTest(unittest.TestCase):
    """列名匹配：精确 → 去空格/全角括号归一化 → 包含（大小写不敏感）。"""

    def test_exact_match(self):
        cols = ["资产总计", "负债合计", "所有者权益(或股东权益)合计"]
        result = match_columns(cols, TABLE_ALIASES["balance"])
        self.assertEqual(result.missing, [])
        self.assertEqual(set(result.matched),
                         {"total_assets", "total_liabilities", "total_equity"})
        self.assertTrue(all(m == "exact" for m in result.methods.values()))

    def test_normalized_match_ignores_spaces_and_fullwidth_parens(self):
        cols = ["资产总计 ", "负债合计", "所有者权益（或股东权益）合计"]
        result = match_columns(cols, TABLE_ALIASES["balance"])
        self.assertEqual(result.columns["total_assets"], "资产总计 ")
        self.assertEqual(result.columns["total_equity"], "所有者权益（或股东权益）合计")
        self.assertEqual(result.methods["total_assets"], "normalized")
        self.assertEqual(result.methods["total_equity"], "normalized")

    def test_contains_match(self):
        result = match_columns(["资产总计(元)"], {"total_assets": ["资产总计"]})
        self.assertEqual(result.columns["total_assets"], "资产总计(元)")
        self.assertEqual(result.methods["total_assets"], "contains")

    def test_contains_is_case_insensitive(self):
        result = match_columns(["Total Revenue"], {"revenue": ["revenue"]})
        self.assertEqual(result.columns["revenue"], "Total Revenue")
        self.assertEqual(result.methods["revenue"], "contains")

    def test_missing_columns_reported(self):
        result = match_columns(["资产总计"], TABLE_ALIASES["balance"])
        self.assertIn("total_liabilities", result.missing)
        self.assertIn("total_equity", result.missing)
        self.assertEqual(result.matched, ["total_assets"])

    def test_one_column_claimed_by_only_one_field(self):
        # “归属于母公司所有者的净利润”只能归 net_profit_attr，不能被净利 net_profit 抢走
        cols = ["归属于母公司所有者的净利润", "净利润"]
        result = match_columns(cols, TABLE_ALIASES["income"])
        self.assertEqual(result.columns["net_profit_attr"], "归属于母公司所有者的净利润")
        self.assertEqual(result.columns["net_profit"], "净利润")

    def test_empty_columns_all_missing(self):
        result = match_columns([], TABLE_ALIASES["balance"])
        self.assertEqual(result.matched, [])
        self.assertEqual(set(result.missing),
                         {"total_assets", "total_liabilities", "total_equity"})


class NormalizeStatementRowTest(unittest.TestCase):
    """一行财报解析：数值可用 + 命中/缺失/不可解析科目可诊断。"""

    def test_values_and_diagnostics(self):
        row = {
            "资产总计": "1,234.56",
            "负债合计": None,
            "所有者权益（或股东权益）合计": "--",
        }
        result = normalize_statement_row(row, TABLE_ALIASES["balance"])
        self.assertEqual(result.values, {"total_assets": 1234.56})
        self.assertEqual(result.missing, [])
        self.assertEqual(set(result.matched),
                         {"total_assets", "total_liabilities", "total_equity"})
        self.assertEqual(set(result.unparsable), {"total_liabilities", "total_equity"})
        diag = result.to_diagnostics()
        self.assertIn("资产总计", diag["matched"])
        self.assertIn("负债合计", diag["unparsable"])
        self.assertIn("资产总计", diag["columns"])

    def test_missing_columns_reported(self):
        result = normalize_statement_row({"资产总计": 100}, TABLE_ALIASES["balance"])
        self.assertIn("total_liabilities", result.missing)
        self.assertIn("负债合计", result.labels_missing)

    def test_dirty_value_parsed(self):
        row = {"营业收入": "1,000.00万元", "所得税费用": "－5"}
        result = normalize_statement_row(row, TABLE_ALIASES["income"])
        self.assertEqual(result.values["revenue"], 1000.0 * 1e4)
        self.assertEqual(result.values["income_tax"], -5.0)

    def test_garbage_value_marked_unparsable_not_raised(self):
        result = normalize_statement_row({"资产总计": object()},
                                         TABLE_ALIASES["balance"])
        self.assertEqual(result.unparsable, ["total_assets"])
        self.assertEqual(result.values, {})

    def test_statements_from_row_keeps_two_scopes_separate(self):
        out = statements_from_row({"净利润": 85.0, "归母净利润": 80.0})
        self.assertEqual(out["income"]["net_profit"], 85.0)
        self.assertEqual(out["income"]["net_profit_attr"], 80.0)

    def test_statements_from_row_tolerates_dirty_columns_and_values(self):
        out = statements_from_row({
            "资产总计（元）": "1,000",
            "负债合计": "400",
            "所有者权益（或股东权益）合计": "600",
        })
        self.assertEqual(out["balance"]["total_assets"], 1000.0)
        self.assertEqual(out["balance"]["total_liabilities"], 400.0)
        self.assertEqual(out["balance"]["total_equity"], 600.0)


class StatementFrameParseTest(unittest.TestCase):
    """research_assistant 的三表解析：字段级诊断 + 缺科目提示。"""

    def test_full_frames_parse_and_pass(self):
        balance = pd.DataFrame([{
            "资产总计": 1000.0, "负债合计": 400.0,
            "所有者权益(或股东权益)合计": 600.0,
        }])
        income = pd.DataFrame([{
            "营业收入": 500.0, "营业总成本": 300.0, "营业利润": 150.0,
            "利润总额": 120.0, "所得税费用": 20.0, "净利润": 100.0,
        }])
        cashflow = pd.DataFrame([{
            "期初现金及现金等价物余额": 100.0, "期末现金及现金等价物余额": 180.0,
            "经营活动产生的现金流量净额": 90.0,
            "投资活动产生的现金流量净额": -20.0,
            "筹资活动产生的现金流量净额": 10.0,
        }])
        report, tables = ResearchAssistant._parse_statement_frames(
            {"balance": balance, "income": income, "cashflow": cashflow}
        )
        self.assertTrue(report.data_sufficient)
        self.assertTrue(report.passed)
        self.assertEqual(tables["balance"]["missing"], [])
        self.assertEqual(tables["balance"]["fetched"], True)
        self.assertIn("资产总计", tables["balance"]["matched"])
        self.assertEqual(tables["balance"]["columns"]["资产总计"], "资产总计")

    def test_missing_columns_produce_explicit_hint(self):
        balance = pd.DataFrame([{"资产总计": 1000.0}])
        _, tables = ResearchAssistant._parse_statement_frames(
            {"balance": balance, "income": None, "cashflow": None}
        )
        diagnostic = ResearchAssistant._statements_diagnostic(tables)
        self.assertIsNotNone(diagnostic)
        self.assertIn("未获取到以下科目", diagnostic)
        self.assertIn("负债合计", diagnostic)
        self.assertIn("建议人工核对", diagnostic)

    def test_fetch_error_recorded_per_table(self):
        _, tables = ResearchAssistant._parse_statement_frames(
            {"balance": None, "income": None, "cashflow": None},
            {"balance": "抓取资产负债表失败：boom"},
        )
        self.assertIn("boom", tables["balance"]["error"])
        self.assertFalse(tables["balance"]["fetched"])
        self.assertIn("未返回", tables["income"]["error"])

    def test_unparsable_value_recorded(self):
        balance = pd.DataFrame([{
            "资产总计": "--", "负债合计": "1,000",
            "所有者权益(或股东权益)合计": "1,000",
        }])
        _, tables = ResearchAssistant._parse_statement_frames(
            {"balance": balance, "income": None, "cashflow": None}
        )
        self.assertIn("资产总计", tables["balance"]["unparsable"])
        diagnostic = ResearchAssistant._statements_diagnostic(tables)
        self.assertIn("无法解析", diagnostic)


class SampleIndustryTest(unittest.TestCase):
    """离线行业样例（F3）：统计值必须来自 CSV，而非硬编码。"""

    def test_csv_exists_with_expected_schema(self):
        self.assertTrue(_SAMPLE_CSV.exists(), "离线行业样例 CSV 缺失")
        df = pd.read_csv(_SAMPLE_CSV, dtype=str)
        self.assertEqual(list(df.columns), ["代码", "名称", "涨跌幅", "总市值"])
        self.assertGreaterEqual(len(df), 8)
        self.assertTrue(all(str(n).startswith("演示") for n in df["名称"]))

    def test_csv_stats_match_expected_values(self):
        df = pd.read_csv(_SAMPLE_CSV, dtype=str)
        pct = pd.to_numeric(df["涨跌幅"], errors="coerce")
        self.assertEqual(len(df), 8)
        self.assertEqual(int((pct > 0).sum()), 4)
        self.assertEqual(round(float(pct.mean()), 2), 0.32)
        # 8 行偶数列中位数 = (0.00 + 0.35) / 2 = 0.175，浮点取整后为 0.17
        self.assertEqual(round(float(pct.median()), 2), 0.17)

    def test_sample_snapshot_is_driven_by_csv(self):
        df = pd.read_csv(_SAMPLE_CSV, dtype=str)
        stats, table, used_sample = ResearchAssistant._sample_industry_snapshot("银行")
        self.assertTrue(used_sample)
        self.assertEqual(stats["count"], len(df))
        self.assertEqual(stats["up_count"], 4)
        self.assertEqual(stats["avg_pct"], 0.32)
        self.assertEqual(stats["median_pct"], 0.17)
        self.assertEqual(stats["leaders"],
                         "演示银行A(2.35%)、演示银行B(1.62%)、演示银行C(0.88%)")
        self.assertIn(_SAMPLE_CSV.name, stats["note"])
        self.assertIn("演示银行A", table)

    def test_online_aggregate_uses_same_convention(self):
        df = pd.read_csv(_SAMPLE_CSV, dtype=str)
        stats, _ = ResearchAssistant._aggregate_industry_df(df)
        sample_stats, _, _ = ResearchAssistant._sample_industry_snapshot("银行")
        for key in ("count", "up_count", "avg_pct", "median_pct", "leaders"):
            self.assertEqual(stats[key], sample_stats[key], f"{key} 口径不一致")

    def test_missing_csv_falls_back_with_warning(self):
        from unittest.mock import patch

        with patch("src.services.research_assistant._SAMPLE_INDUSTRY_CSV",
                   Path("/nonexistent/sample_industry_raw.csv")):
            stats, _table, used_sample = ResearchAssistant._sample_industry_snapshot("银行")
        self.assertTrue(used_sample)
        self.assertEqual(stats["count"], 0)
        self.assertIn("缺失", stats["note"])

    def test_generator_reproduces_committed_csv_byte_for_byte(self):
        module = _load_generator()
        generated = module.gen_industry().to_csv(index=False).encode("utf-8-sig")
        self.assertEqual(generated, _SAMPLE_CSV.read_bytes(),
                         "生成器产物与仓库 CSV 不一致，请重跑 scripts/generate_samples.py")


if __name__ == "__main__":
    unittest.main()
