"""数据源容灾 + 接口级降级的离线测试（全程不联网）。

覆盖两层降级：
1. **数据源级**：``DataSourceManager`` 按完备度顺序换整个数据源；
2. **接口级**：``AKShareSource`` 内部换 akshare 函数（东财 → 新浪 → 腾讯）。

所有 akshare 调用都被 ``_StubAk`` 顶替，重试次数设成 1、退避设成 0，
保证测试既快又不会真的打到网络上。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import pandas as pd

from src.data.cleaner import clean_financial
from src.data.manager import (
    DataSourceManager,
    build_manager_from_config,
    build_manager_from_settings,
    default_source_names,
)
from src.data.sources import (
    DOMESTIC_FINANCE_HOSTS,
    FREE_SOURCE_ORDER,
    AKShareSource,
    DataSourceBase,
    DataSourceError,
    _adapt_em_financial_abstract,
    _adapt_em_financial_indicator,
    _adapt_sina_income_statement,
    _slice_by_date,
    bypass_system_proxy_for_domestic,
)


class _StubAk:
    """按函数名分发的假 akshare 模块，并记录调用顺序。"""

    def __init__(self, results: dict):
        self.results = results
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def _call(**kwargs):  # noqa: ARG001 - 只关心函数名
            self.calls.append(name)
            value = self.results.get(name)
            if value is None:
                raise DataSourceError(f"假接口 {name} 未配置")
            if isinstance(value, BaseException):
                raise value
            return value.copy() if hasattr(value, "copy") else value

        return _call


def _price_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2025-11-03", "2025-11-04"],
            "open": [10.0, 10.5],
            "high": [11.0, 11.2],
            "low": [9.8, 10.2],
            "close": [10.5, 11.1],
            "volume": [1000, 1200],
        }
    )


class FreeSourceOrderTest(unittest.TestCase):
    def test_order_is_ranked_by_completeness(self):
        # akshare 提供 13 类接口，新浪/腾讯各 3 类，所以排在前面
        self.assertEqual(FREE_SOURCE_ORDER, ["akshare", "sina_http", "tencent_http"])

    def test_default_source_names_returns_copy(self):
        names = default_source_names()
        names.append("offline")
        self.assertNotIn("offline", FREE_SOURCE_ORDER)

    def test_empty_config_uses_ranked_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            manager = build_manager_from_config({})
        self.assertEqual(manager.source_names, FREE_SOURCE_ORDER)
        self.assertTrue(manager.auto_failover)

    def test_settings_without_sources_still_builds_ranked_manager(self):
        class _Settings:
            data_sources: list[str] = []
            cross_check_enabled = True
            auto_failover = True
            mcp_server_url = ""

        manager = build_manager_from_settings(_Settings())
        self.assertIsNotNone(manager)
        self.assertEqual(manager.source_names, FREE_SOURCE_ORDER)


class PriceFallbackTest(unittest.TestCase):
    def _source(self, results: dict) -> AKShareSource:
        source = AKShareSource(retry=1, backoff=0)
        source._ak = _StubAk(results)
        return source

    def test_falls_back_to_sina_when_eastmoney_fails(self):
        source = self._source(
            {
                "stock_zh_a_hist": DataSourceError("东财被限流"),
                "stock_zh_a_daily": _price_frame(),
            }
        )
        out = source.price_daily("002428", "20250101", "20261231")
        self.assertEqual(list(out["close"]), [10.5, 11.1])
        self.assertEqual(source._ak.calls, ["stock_zh_a_hist", "stock_zh_a_daily"])

    def test_sina_needs_exchange_prefixed_symbol(self):
        source = self._source(
            {
                "stock_zh_a_hist": DataSourceError("东财被限流"),
                "stock_zh_a_daily": _price_frame(),
            }
        )
        seen: dict = {}

        def _daily(**kwargs):
            seen.update(kwargs)
            return _price_frame()

        source._ak.stock_zh_a_daily = _daily
        source.price_daily("002428", "20250101", "20261231")
        self.assertEqual(seen["symbol"], "sz002428")

    def test_falls_back_to_tencent_when_both_fail(self):
        source = self._source(
            {
                "stock_zh_a_hist": DataSourceError("东财被限流"),
                "stock_zh_a_daily": DataSourceError("新浪被限流"),
                "stock_zh_a_hist_tx": _price_frame(),
            }
        )
        out = source.price_daily("002428", "20250101", "20261231")
        self.assertFalse(out.empty)
        self.assertEqual(
            source._ak.calls,
            ["stock_zh_a_hist", "stock_zh_a_daily", "stock_zh_a_hist_tx"],
        )

    def test_all_interfaces_failing_raises_with_details(self):
        source = self._source(
            {
                "stock_zh_a_hist": DataSourceError("东财被限流"),
                "stock_zh_a_daily": DataSourceError("新浪被限流"),
                "stock_zh_a_hist_tx": DataSourceError("腾讯被限流"),
            }
        )
        with self.assertRaises(DataSourceError) as ctx:
            source.price_daily("002428", "20250101", "20261231")
        self.assertIn("所有候选接口均失败", str(ctx.exception))
        self.assertIn("东财日线", str(ctx.exception))


class FinancialFallbackTest(unittest.TestCase):
    def _long_abstract(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "选项": ["常用指标", "常用指标", "盈利能力"],
                "指标": ["营业总收入", "归母净利润", "营业总收入"],
                "20250930": [1.0e9, 8.0e7, 1.0e9],
                "20250630": [5.0e8, 4.0e7, 5.0e8],
            }
        )

    def test_falls_back_to_eastmoney_abstract(self):
        source = AKShareSource(retry=1, backoff=0)
        source._ak = _StubAk(
            {
                "stock_financial_abstract_ths": DataSourceError("同花顺挂了"),
                "stock_financial_abstract": self._long_abstract(),
            }
        )
        clean = clean_financial(source.financial_abstract("002428"))
        self.assertEqual(len(clean), 2)
        latest = clean.iloc[0]
        self.assertAlmostEqual(latest["revenue"], 1.0e9)
        self.assertAlmostEqual(latest["net_profit"], 8.0e7)

    def test_long_abstract_dedupes_repeated_indicators(self):
        wide = _adapt_em_financial_abstract(self._long_abstract())
        # 「营业总收入」在常用指标和盈利能力下都出现，只能保留一份
        self.assertEqual(list(wide.columns).count("营业总收入"), 1)
        self.assertEqual(len(wide), 2)

    def test_adapt_indicator_keeps_only_known_columns(self):
        raw = pd.DataFrame(
            {
                "日期": ["2025-09-30"],
                "摊薄每股收益(元)": [0.12],
                "销售毛利率(%)": [21.5],
                "净资产收益率(%)": [3.4],
                "无关列": [999],
            }
        )
        clean = clean_financial(_adapt_em_financial_indicator(raw))
        row = clean.iloc[0]
        self.assertAlmostEqual(row["eps"], 0.12)
        self.assertAlmostEqual(row["gross_margin"], 21.5)
        self.assertAlmostEqual(row["roe"], 3.4)
        # 没有营收/净利就应该真的没有，绝不臆造
        self.assertNotIn("revenue", clean.columns)
        self.assertNotIn("net_profit", clean.columns)

    def test_adapt_sina_income_statement_prefers_parent_net_profit(self):
        raw = pd.DataFrame(
            {
                "报告日": ["20250930"],
                "营业总收入": [1.0e9],
                "净利润": [9.0e7],
                "归属于母公司所有者的净利润": [8.0e7],
            }
        )
        clean = clean_financial(_adapt_sina_income_statement(raw))
        self.assertAlmostEqual(clean.iloc[0]["net_profit"], 8.0e7)

    def test_adapters_reject_incompatible_frames(self):
        self.assertTrue(_adapt_em_financial_abstract(pd.DataFrame({"a": [1]})).empty)
        self.assertTrue(_adapt_em_financial_indicator(pd.DataFrame({"a": [1]})).empty)
        self.assertTrue(_adapt_sina_income_statement(pd.DataFrame({"a": [1]})).empty)


class SliceByDateTest(unittest.TestCase):
    def test_slices_full_history_to_requested_range(self):
        df = pd.DataFrame(
            {"date": ["2024-01-02", "2025-11-03", "2025-11-04"], "close": [1.0, 2.0, 3.0]}
        )
        out = _slice_by_date(df, "20250101", "20251231")
        self.assertEqual(list(out["close"]), [2.0, 3.0])

    def test_returns_original_when_result_would_be_empty(self):
        df = pd.DataFrame({"date": ["2024-01-02"], "close": [1.0]})
        # 全被切掉就交回原表，由 cleaner 决定怎么处理
        self.assertEqual(len(_slice_by_date(df, "20250101", "20251231")), 1)

    def test_accepts_yyyymmdd_arguments(self):
        df = pd.DataFrame(
            {"date": ["2025-11-03", "2025-11-04"], "close": [2.0, 3.0]}
        )
        self.assertEqual(len(_slice_by_date(df, "20251104", "20251231")), 1)

    def test_passthrough_without_date_column(self):
        df = pd.DataFrame({"close": [1.0]})
        self.assertEqual(len(_slice_by_date(df, "20250101", "20251231")), 1)


class StockInfoFallbackTest(unittest.TestCase):
    def test_falls_back_to_spot_snapshot(self):
        source = AKShareSource(retry=1, backoff=0)
        spot = pd.DataFrame(
            {"代码": ["sh600519", "sz002428"], "名称": ["贵州茅台", "云南锗业"], "最新价": [1500, 20]}
        )
        source._ak = _StubAk(
            {
                "stock_individual_info_em": DataSourceError("东财挂了"),
                "stock_zh_a_spot": spot,
            }
        )
        info = source.stock_info("002428")
        mapping = dict(zip(info["item"], info["value"]))
        self.assertEqual(mapping["名称"], "云南锗业")


class _FakeSource(DataSourceBase):
    """可控的假数据源：成功返回固定值，失败抛 DataSourceError。"""

    def __init__(self, name: str, payload, fail: bool = False):
        self.name = name
        self.payload = payload
        self.fail = fail
        self.calls = 0

    def price_daily(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls += 1
        if self.fail:
            raise DataSourceError(f"{self.name} 不可用")
        return self.payload

    def financial_abstract(self, symbol):
        self.calls += 1
        if self.fail:
            raise DataSourceError(f"{self.name} 不可用")
        return self.payload

    def stock_info(self, symbol):
        return pd.DataFrame({"item": ["股票简称"], "value": ["测试"]})


class ManagerFailoverTest(unittest.TestCase):
    def test_switches_to_next_source_and_sticks(self):
        bad = _FakeSource("bad", None, fail=True)
        good = _FakeSource("good", _price_frame())
        manager = DataSourceManager(sources=[bad, good], enable_cross_check=False)

        data, name = manager.fetch_with_failover(
            "price_daily", symbol="002428", start_date="20250101", end_date="20261231"
        )
        self.assertEqual(name, "good")
        self.assertFalse(data.empty)

        # 第二次直接从上次成功的源开始，不再白试一次坏源
        _, name = manager.fetch_with_failover(
            "price_daily", symbol="002428", start_date="20250101", end_date="20261231"
        )
        self.assertEqual(name, "good")
        self.assertEqual(bad.calls, 1)

    def test_all_sources_failing_raises(self):
        manager = DataSourceManager(
            sources=[_FakeSource("a", None, fail=True), _FakeSource("b", None, fail=True)],
            enable_cross_check=False,
        )
        with self.assertRaises(DataSourceError) as ctx:
            manager.fetch_with_failover("price_daily", symbol="002428")
        self.assertIn("所有数据源都取数失败", str(ctx.exception))

    def test_auto_failover_disabled_only_tries_primary(self):
        bad = _FakeSource("bad", None, fail=True)
        good = _FakeSource("good", _price_frame())
        manager = DataSourceManager(
            sources=[bad, good], enable_cross_check=False, auto_failover=False
        )
        with self.assertRaises(DataSourceError):
            manager.fetch_with_failover("price_daily", symbol="002428")
        self.assertEqual(good.calls, 0)


class ProxyBypassTest(unittest.TestCase):
    def test_sets_no_proxy_for_domestic_hosts(self):
        with mock.patch.dict(os.environ, {"no_proxy": "localhost"}, clear=False):
            os.environ.pop("NO_PROXY", None)
            changed = bypass_system_proxy_for_domestic(True)
            self.assertTrue(changed)
            entries = os.environ["no_proxy"].split(",")
            self.assertIn("localhost", entries)          # 已有的不能丢
            for host in DOMESTIC_FINANCE_HOSTS:
                self.assertIn(host, entries)

    def test_disabled_changes_nothing(self):
        with mock.patch.dict(os.environ, {"no_proxy": "localhost"}, clear=False):
            os.environ.pop("NO_PROXY", None)
            self.assertFalse(bypass_system_proxy_for_domestic(False))
            self.assertEqual(os.environ["no_proxy"], "localhost")


if __name__ == "__main__":
    unittest.main()
