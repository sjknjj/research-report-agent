"""数据清洗模块测试。"""
import math
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.data import cache, sample_data
from src.data.cleaner import (
    clean_financial,
    clean_price,
    parse_cn_number,
    parse_percent,
    summarize_financial,
    summarize_price,
)
from src.data.data_service import CompanyBundle, DataService


class ParseNumberTest(unittest.TestCase):
    def test_cn_amount(self):
        self.assertAlmostEqual(parse_cn_number("1.47亿"), 1.47e8)
        self.assertAlmostEqual(parse_cn_number("2.50万"), 2.5e4)
        self.assertAlmostEqual(parse_cn_number("1.2万亿"), 1.2e12)

    def test_percent(self):
        self.assertAlmostEqual(parse_percent("46.84%"), 46.84)
        self.assertAlmostEqual(parse_percent("-1.95%"), -1.95)

    def test_missing(self):
        for token in ("False", "--", "", None, False):
            self.assertTrue(math.isnan(parse_cn_number(token)))


class CleanFromSampleTest(unittest.TestCase):
    def setUp(self):
        self.price = clean_price(sample_data.sample_price_raw())
        self.fin = clean_financial(sample_data.sample_financial_raw())

    def test_clean_financial(self):
        latest = self.fin.iloc[0]
        self.assertEqual(latest["period"].strftime("%Y-%m-%d"), "2025-12-31")
        self.assertAlmostEqual(latest["revenue"], 1.523e10, delta=1)
        self.assertAlmostEqual(latest["net_profit"], 3.18e9, delta=1)
        self.assertAlmostEqual(latest["gross_margin"], 42.3)
        # False 缺失应被清洗为 NaN
        self.assertTrue(np.isnan(self.fin.iloc[-1]["net_profit_deducted"]))

    def test_summarize_financial(self):
        summary = summarize_financial(self.fin)
        self.assertEqual(summary["latest_period"], "2025-12-31")
        self.assertAlmostEqual(summary["latest"]["revenue_yuan"], 1.523e10, delta=1)
        self.assertEqual(len(summary["recent_periods"]), 5)

    def test_clean_price(self):
        self.assertFalse(self.price["date"].duplicated().any())
        self.assertTrue((self.price["date"].diff().dropna() > np.timedelta64(0)).all())
        self.assertIn("ma20", self.price.columns)
        summary = summarize_price(self.price)
        self.assertEqual(summary["trading_days"], 120)
        self.assertIsNotNone(summary["latest_close"])


class CleanerEdgeCaseTest(unittest.TestCase):
    def test_all_nan_amount_gives_none(self):
        """全 NaN 成交额不能求和成 0.0。"""
        price = clean_price(sample_data.sample_price_raw())
        price["amount"] = np.nan
        summary = summarize_price(price)
        self.assertIsNone(summary["total_amount_yuan"])

    def test_prefers_huigui_net_profit(self):
        """同时出现时，net_profit 应取『归母净利润』而不是『净利润』。"""
        df = pd.DataFrame(
            {"报告期": ["2025-12-31"], "净利润": ["1.00亿"], "归母净利润": ["2.00亿"]}
        )
        clean = clean_financial(df)
        self.assertAlmostEqual(clean.iloc[0]["net_profit"], 2.0e8)

    def test_parse_percent_keeps_scale(self):
        """百分比口径固定为百分数数值，0.42 不做 ×100 换算。"""
        self.assertAlmostEqual(parse_percent("46.84%"), 46.84)
        self.assertAlmostEqual(parse_percent(0.42), 0.42)
        self.assertAlmostEqual(parse_percent("0.42"), 0.42)


class CacheFrameTest(unittest.TestCase):
    """缓存必须携带清洗后的 DataFrame，且缓存键包含区间与复权方式。"""

    @classmethod
    def setUpClass(cls):
        cls.price = clean_price(sample_data.sample_price_raw())
        cls.fin = clean_financial(sample_data.sample_financial_raw())
        cls.end = datetime.now().strftime("%Y%m%d")
        cls.start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        cls.saved_dir = cache.CACHE_DIR

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        cache.CACHE_DIR = pathlib.Path(self._tmp.name)
        bundle = CompanyBundle(
            symbol="000001",
            name="缓存测试",
            source="live",
            as_of="2026-01-01 00:00:00",
            price_summary=summarize_price(self.price),
            financial_summary=summarize_financial(self.fin),
            price_clean=self.price,
            financial_clean=self.fin,
        )
        cache.save_cached_bundle(
            "000001", bundle.to_dict(),
            start_date=self.start, end_date=self.end, adjust="qfq",
            price_clean=self.price, financial_clean=self.fin,
        )

    def tearDown(self):
        cache.CACHE_DIR = self.saved_dir
        self._tmp.cleanup()

    def test_cache_key_includes_range_and_adjust(self):
        self.assertIsNotNone(cache.get_cached_bundle("000001", self.start, self.end, "qfq"))
        self.assertIsNone(cache.get_cached_bundle("000001", "20200101", self.end, "qfq"))
        self.assertIsNone(cache.get_cached_bundle("000001", self.start, self.end, "hfq"))

    def test_cache_hit_rebuilds_dataframes(self):
        cached = cache.get_cached_bundle("000001", self.start, self.end, "qfq")
        self.assertIsInstance(cached["price_clean"], pd.DataFrame)
        self.assertIsInstance(cached["financial_clean"], pd.DataFrame)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(cached["price_clean"]["date"]))
        self.assertEqual(len(cached["price_clean"]), len(self.price))

    def test_service_cache_hit_keeps_dataframes(self):
        """缓存命中后 price_clean / financial_clean 仍可用（K 线/财务图不丢）。"""
        service = DataService()
        bundle = service.get_company_bundle(
            "000001", start_date=self.start, end_date=self.end, prefer_live=True
        )
        self.assertIsNotNone(bundle.price_clean)
        self.assertIsNotNone(bundle.financial_clean)
        self.assertFalse(bundle.price_clean.empty)
        self.assertFalse(bundle.financial_clean.empty)

    def test_old_or_broken_cache_is_miss_not_crash(self):
        """旧/残缺缓存应被视为未命中，而不是 KeyError。"""
        path = cache._cache_path("000002", self.start, self.end, "qfq")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"symbol": "000002", "source": "live"}', encoding="utf-8")
        self.assertIsNone(cache.get_cached_bundle("000002", self.start, self.end, "qfq"))


class DataSourceManagerTest(unittest.TestCase):
    def test_auto_failover_false_does_not_switch(self):
        from src.data.manager import DataSourceManager
        from src.data.sources import DataSourceError

        class Bad:
            name = "bad"

            def price_daily(self, **kwargs):
                raise RuntimeError("主源不可用")

        class Good:
            name = "good"

            def price_daily(self, **kwargs):
                return "ok"

        strict = DataSourceManager([Bad(), Good()], auto_failover=False)
        with self.assertRaises(DataSourceError):
            strict.fetch_with_failover("price_daily", symbol="000001")

        tolerant = DataSourceManager([Bad(), Good()], auto_failover=True)
        data, name = tolerant.fetch_with_failover("price_daily", symbol="000001")
        self.assertEqual((data, name), ("ok", "good"))
        # 成功切到备源后应“粘”在备源上
        self.assertEqual(tolerant._current_idx, 1)

    def test_cross_check_uses_symmetric_error(self):
        from src.data.manager import DataSourceManager
        from src.data.sources import AKShareSource

        manager = DataSourceManager([AKShareSource()], enable_cross_check=True)
        self.assertTrue(
            manager.cross_check_numbers({"revenue_yuan": 100.0}, {"revenue_yuan": 50.0})
        )
        self.assertFalse(
            manager.cross_check_numbers({"revenue_yuan": 100.0}, {"revenue_yuan": 99.5})
        )

    def test_offline_source_returns_samples(self):
        from src.data.sources import OfflineSource

        source = OfflineSource()
        self.assertFalse(source.price_daily("000001", "20200101", "20201231").empty)
        self.assertFalse(source.financial_abstract("000001").empty)
        self.assertFalse(source.stock_info("000001").empty)

    def test_exchange_inference_covers_bse(self):
        from src.data.sources import _exchange

        self.assertEqual(_exchange("600519"), "SH")
        self.assertEqual(_exchange("000001"), "SZ")
        self.assertEqual(_exchange("430047"), "BJ")
        self.assertEqual(_exchange("830799"), "BJ")
        self.assertEqual(_exchange("920001"), "BJ")

    def test_data_service_cross_check_reaches_bundle(self):
        """DataService 应真正调用多源交叉验证，并把警告挂到 bundle.cross_check。"""
        from src.data.manager import DataSourceManager

        class SourceA:
            name = "source_a"

            def financial_abstract(self, symbol):
                return pd.DataFrame({"报告期": ["2025-12-31"], "营业总收入": ["100亿"]})

        class SourceB:
            name = "source_b"

            def financial_abstract(self, symbol):
                return pd.DataFrame({"报告期": ["2025-12-31"], "营业总收入": ["50亿"]})

        primary = clean_financial(
            pd.DataFrame({"报告期": ["2025-12-31"], "营业总收入": ["100亿"]})
        )
        manager = DataSourceManager([SourceA(), SourceB()], enable_cross_check=True)
        warnings = DataService(manager=manager)._cross_check_financials("000001", primary)
        self.assertTrue(warnings)
        self.assertIn("营收", warnings[0])


if __name__ == "__main__":
    unittest.main()
