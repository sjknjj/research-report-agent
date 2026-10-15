"""数据清洗模块测试。"""
import math
import unittest

import numpy as np

from src.data import sample_data
from src.data.cleaner import (
    clean_financial,
    clean_price,
    parse_cn_number,
    parse_percent,
    summarize_financial,
    summarize_price,
)


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


if __name__ == "__main__":
    unittest.main()
