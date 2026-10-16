"""analysis 子模块单元测试：backtest / comparison / sentiment / indicators / chain。

全部离线运行：不联网、不需要 API Key，行情用确定性合成序列，
公司数据用 DataService(prefer_live=False) 读取 data/samples 下的虚构样例。
"""
import json
import math
import unittest
from functools import lru_cache

import numpy as np
import pandas as pd

from src.analysis import indicators
from src.analysis.backtest import (
    FEE_BUY,
    FEE_SELL,
    _max_drawdown_pct,
    backtest_macd,
)
from src.analysis.chain import (
    CHAIN_DB,
    _normalize_code,
    chain_to_text,
    find_chain,
    list_chains,
)
from src.analysis.comparison import (
    RADAR_DIMENSIONS,
    CompanyRow,
    ComparisonService,
    _radar_scores,
    _valuation_percentile_rows,
    factor_analysis,
    radar_figure_json,
    valuation_figure_json,
)
from src.analysis.sentiment import analyze_sentiment
from src.data.data_service import DataService
from src.data.sources import DataSourceError


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------
def _price_df(closes, start="2024-01-01"):
    """由收盘价序列构造带 open/close/high/low/date 的行情。"""
    closes = [float(c) for c in closes]
    n = len(closes)
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d").tolist()
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "close": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
    })


def _decline_then_rise():
    """先跌后涨：结束时必然持有（无死叉）→ 触发期末强制平仓（盈利）。"""
    down = list(np.linspace(40, 20, 45))
    up = list(np.linspace(20, 35, 45))
    return down + up


def _decline_rise_fall():
    """先跌、反弹出现金叉、随后小幅回落：期末持有 → 强制平仓（亏损）。"""
    return (list(np.linspace(40, 20, 45))
            + list(np.linspace(20, 26, 12))
            + list(np.linspace(26, 21, 6)))


def _cycle_with_normal_close():
    """先跌后涨再跌：出现一次完整金叉→死叉（普通平仓，计入手续费）。"""
    return (list(np.linspace(40, 20, 45))
            + list(np.linspace(20, 30, 25))
            + list(np.linspace(30, 18, 45)))


@lru_cache(maxsize=1)
def _sample_bundle():
    return DataService().get_company_bundle("600519", prefer_live=False, use_cache=False)


# ===========================================================================
# backtest.py
# ===========================================================================
class BacktestTest(unittest.TestCase):
    def test_empty_result_when_data_insufficient(self):
        result = backtest_macd(_price_df(list(range(20, 30))))
        self.assertIn("error", result)
        self.assertEqual(result["total_trades"], 0)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["win_rate"], 0.0)
        self.assertEqual(result["max_drawdown_pct"], 0.0)

    def test_empty_result_when_close_missing(self):
        df = _price_df(list(range(40, 90))).drop(columns=["close"])
        result = backtest_macd(df)
        self.assertIn("close", result["error"])

    def test_max_drawdown_is_percentage_not_ratio(self):
        # 100 → 80 → 120 → 60：最大回撤发生在 120 → 60，即 -50%
        self.assertEqual(_max_drawdown_pct([100, 80, 120, 60]), -50.0)
        self.assertEqual(_max_drawdown_pct([100, 100, 100]), 0.0)
        self.assertEqual(_max_drawdown_pct([]), 0.0)

    def test_ratios_are_percentages_not_double_scaled(self):
        """回归：策略收益率/单笔收益必须是百分数（几十），不能被再 ×100 成几千。"""
        result = backtest_macd(_price_df(_decline_then_rise()))
        self.assertGreaterEqual(result["total_trades"], 1)
        # 合成序列收盘 20 → 35，单笔收益约 +69%，绝不应是 6900
        self.assertGreater(result["strategy_return_pct"], 50)
        self.assertLess(result["strategy_return_pct"], 100)
        self.assertGreaterEqual(result["win_rate"], 0.0)
        self.assertLessEqual(result["win_rate"], 100.0)
        self.assertLess(result["max_drawdown_pct"], 0.0)
        self.assertGreater(result["max_drawdown_pct"], -100.0)
        for trade in result["positions"]:
            if "profit_pct" in trade:
                self.assertLess(abs(trade["profit_pct"]), 200)

    def test_buy_hold_and_total_return_fields(self):
        closes = _decline_then_rise()
        result = backtest_macd(_price_df(closes))
        expected_bh = (closes[-1] / closes[0] - 1) * 100
        self.assertAlmostEqual(result["buy_hold_return_pct"], expected_bh, places=2)
        # 旧字段名 total_return 与 strategy_return_pct 同义，且不是被 ×100 的版本
        self.assertEqual(result["total_return"], result["strategy_return_pct"])
        self.assertLess(abs(result["total_return"]), 200)

    def test_fees_are_actually_deducted(self):
        result = backtest_macd(_price_df(_cycle_with_normal_close()))
        fees = result["fees"]
        self.assertEqual(fees["buy"], FEE_BUY)
        self.assertEqual(fees["sell"], FEE_SELL)
        self.assertGreater(fees["sell"], fees["buy"])  # 卖出含印花税

        trades = result["positions"]
        buys = [t for t in trades if t["type"] == "buy"]
        sells = [t for t in trades if t["type"] == "sell"]
        self.assertTrue(buys and sells, "应产生完整的买入/卖出记录")

        buy_px = buys[0]["price"]
        sell_px = sells[0]["price"]
        gross = (sell_px - buy_px) / buy_px * 100
        net = sells[0]["profit_pct"]
        # 净收益必须小于毛收益，差额约等于卖出费率（0.125%）× 价格比
        self.assertLess(net, gross)
        self.assertAlmostEqual(gross - net, FEE_SELL * (sell_px / buy_px) * 100, places=1)
        self.assertGreater(gross - net, 0.05)

    def test_forced_close_win_counted_in_win_rate(self):
        result = backtest_macd(_price_df(_decline_then_rise()))
        sells = [t for t in result["positions"] if t["type"] == "sell"]
        self.assertTrue(sells)
        last = sells[-1]
        self.assertTrue(last.get("forced_close"))
        self.assertGreater(last["profit_pct"], 0)
        self.assertEqual(result["total_trades"], 1)
        self.assertEqual(result["win_trades"], 1)
        self.assertEqual(result["win_rate"], 100.0)

    def test_forced_close_loss_counted_in_lose_trades(self):
        result = backtest_macd(_price_df(_decline_rise_fall()))
        sells = [t for t in result["positions"] if t["type"] == "sell"]
        self.assertTrue(sells)
        self.assertTrue(sells[-1].get("forced_close"))
        self.assertLess(sells[-1]["profit_pct"], 0)
        self.assertEqual(result["lose_trades"], 1)
        self.assertEqual(result["win_trades"], 0)
        self.assertEqual(result["win_rate"], 0.0)
        self.assertLess(result["strategy_return_pct"], 0)

    def test_positions_are_json_serializable(self):
        result = backtest_macd(_price_df(_decline_then_rise()))
        dumped = json.dumps(result["positions"], ensure_ascii=False)
        loaded = json.loads(dumped)
        self.assertEqual(len(loaded), len(result["positions"]))
        for trade in loaded:
            self.assertIsInstance(trade["date"], str)
            self.assertIn(trade["type"], ("buy", "sell"))
            self.assertIsInstance(trade["price"], (int, float))

    def test_falls_back_to_close_when_open_missing(self):
        df = _price_df(_decline_then_rise()).drop(columns=["open"])
        result = backtest_macd(df)
        self.assertGreaterEqual(result["total_trades"], 1)
        self.assertNotIn("error", result)


# ===========================================================================
# comparison.py
# ===========================================================================
class ComparisonTest(unittest.TestCase):
    def _service(self):
        return ComparisonService(DataService())

    def test_compare_populates_numeric_fields(self):
        """回归：键名对不上时整张对比表会退化为全 None，这里必须真的有数值。"""
        rows = self._service().compare(["600519"], prefer_live=False)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.symbol, "600519")
        self.assertEqual(row.source, "sample")

        self.assertEqual(row.revenue_yoy, 12.5)
        self.assertEqual(row.profit_yoy, 9.4)
        self.assertEqual(row.gross_margin, 42.3)
        self.assertEqual(row.roe, 15.6)
        self.assertAlmostEqual(row.period_return, -7.35, places=2)
        self.assertIsNotNone(row.revenue)
        self.assertIsNotNone(row.net_profit)
        self.assertGreater(row.revenue, row.net_profit)

        populated = [v for v in vars(row).values() if v is not None]
        self.assertGreaterEqual(len(populated), 8, "数值字段不应几乎全为空")
        json.dumps(vars(row), ensure_ascii=False)  # 可序列化

    def test_compare_handles_data_source_error(self):
        class _Boom:
            def get_company_bundle(self, *a, **k):
                raise DataSourceError("抓取失败")

        rows = ComparisonService(_Boom()).compare(["600000"], prefer_live=True)
        self.assertEqual(len(rows), 1)
        self.assertIn("抓取失败", rows[0].name)

    def test_rank_table_descending_puts_none_last(self):
        rows = [
            CompanyRow("a", "A", profit_yoy=5.0),
            CompanyRow("b", "B", profit_yoy=None),
            CompanyRow("c", "C", profit_yoy=20.0),
        ]
        ranked = self._service().rank_table(rows, by="profit_yoy")
        self.assertEqual([r.symbol for r in ranked], ["c", "a", "b"])
        self.assertIsNone(ranked[-1].profit_yoy)

    def test_rank_table_pe_ascending_and_none_last(self):
        rows = [
            CompanyRow("a", "A", pe=None),
            CompanyRow("b", "B", pe=30.0),
            CompanyRow("c", "C", pe=10.0),
        ]
        ranked = self._service().rank_table(rows, by="pe")
        self.assertEqual([r.symbol for r in ranked], ["c", "b", "a"])

    def test_rank_table_unknown_key_does_not_crash(self):
        rows = [CompanyRow("a", "A", pe=1.0), CompanyRow("b", "B", pe=2.0)]
        ranked = self._service().rank_table(rows, by="not_a_field")
        self.assertEqual(len(ranked), 2)

    def test_factor_analysis_scores_are_0_to_10(self):
        row = CompanyRow("600519", "贵州茅台", pe=10.0, pb=1.0,
                         revenue_yoy=25.0, profit_yoy=25.0,
                         gross_margin=50.0, roe=20.0,
                         period_return=20.0, signal_count=5)
        result = factor_analysis([row])[0]
        # PE=10 且 PB=1 都是满分 10，增速/盈利同样打到上限 10
        self.assertEqual(result["value_factor"], 10.0)
        self.assertEqual(result["growth_factor"], 10.0)
        self.assertEqual(result["profit_factor"], 10.0)
        self.assertEqual(result["momentum_factor"], 10.0)
        self.assertEqual(result["total_score"], 10.0)
        self.assertEqual(result["missing"], [])
        for factor in result["factors"]:
            self.assertIsNotNone(factor["score"])
            self.assertGreaterEqual(factor["score"], 0.0)
            self.assertLessEqual(factor["score"], 10.0)
        self.assertLessEqual(result["total_score"], 10.0)

    def test_factor_analysis_clamps_low_scores_at_zero(self):
        row = CompanyRow("x", "X", pe=1000.0, pb=1000.0,
                         revenue_yoy=-1000.0, profit_yoy=-1000.0,
                         gross_margin=-1000.0, roe=-1000.0,
                         period_return=-1000.0, signal_count=0)
        result = factor_analysis([row])[0]
        for factor in result["factors"]:
            self.assertEqual(factor["score"], 0.0)
        self.assertEqual(result["total_score"], 0.0)

    def test_factor_analysis_missing_dimension_excluded_from_weight(self):
        """只有成长维度可用时，综合分应等于成长分，而不是被缺失维度拉低。"""
        row = CompanyRow("x", "X", revenue_yoy=0.0, profit_yoy=0.0)
        result = factor_analysis([row])[0]
        self.assertAlmostEqual(result["growth_factor"], 5.0, places=1)
        self.assertEqual(result["total_score"], 5.0)
        for name in ("估值", "盈利", "动量"):
            self.assertIn(name, result["missing"])
        none_scores = [f for f in result["factors"] if f["score"] is None]
        self.assertEqual({f["name"] for f in none_scores}, {"估值", "盈利", "动量"})

    def test_factor_analysis_pe_negative_is_not_full_mark(self):
        """回归：亏损公司（PE<=0）不能被当成『最便宜』拿满分。"""
        loss = factor_analysis([CompanyRow("loss", "亏损", pe=-5.0)])[0]
        self.assertIsNone(loss["value_factor"])
        self.assertIn("估值", loss["missing"])

        cheap = factor_analysis([CompanyRow("cheap", "便宜", pe=10.0)])[0]
        self.assertEqual(cheap["value_factor"], 10.0)

    def test_factor_analysis_sorted_with_none_last(self):
        good = CompanyRow("g", "Good", pe=10.0, pb=1.0, revenue_yoy=30.0,
                          profit_yoy=30.0, gross_margin=60.0, roe=25.0,
                          period_return=15.0, signal_count=1)
        empty = CompanyRow("e", "Empty")
        results = factor_analysis([empty, good])
        self.assertEqual(results[0]["symbol"], "g")
        self.assertIsNone(results[-1]["total_score"])

    def test_radar_scores_pe_non_positive_is_neutral(self):
        self.assertEqual(_radar_scores(CompanyRow("x", "X", pe=-5.0))[0], 50.0)
        self.assertEqual(_radar_scores(CompanyRow("x", "X", pe=None))[0], 50.0)
        self.assertEqual(_radar_scores(CompanyRow("x", "X", pe=10.0))[0], 100.0)
        for score in _radar_scores(CompanyRow("x", "X", pe=10.0, revenue_yoy=30.0)):
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)

    def test_radar_figure_json_structure(self):
        rows = [CompanyRow("a", "A", pe=10.0, revenue_yoy=20.0, profit_yoy=10.0,
                           gross_margin=40.0, roe=15.0, period_return=5.0, signal_count=1)]
        fig = radar_figure_json(rows)
        if fig is None:  # plotly 未安装时的诚实降级
            self.skipTest("plotly 未安装")
        self.assertIn("data", fig)
        self.assertEqual(len(fig["data"]), 1)
        self.assertEqual(len(fig["data"][0]["r"]), len(RADAR_DIMENSIONS))
        self.assertEqual(list(fig["data"][0]["theta"]), RADAR_DIMENSIONS)

    def test_valuation_percentile_cheaper_is_higher(self):
        rows = [
            CompanyRow("a", "A", pe=10.0, pb=1.0),
            CompanyRow("b", "B", pe=20.0, pb=2.0),
            CompanyRow("c", "C", pe=30.0, pb=3.0),
        ]
        data = {d["symbol"]: d for d in _valuation_percentile_rows(rows)}
        self.assertEqual(data["a"]["pe_cheap_pct"], 100.0)
        self.assertEqual(data["b"]["pe_cheap_pct"], 50.0)
        self.assertEqual(data["c"]["pe_cheap_pct"], 0.0)

    def test_valuation_percentile_single_company_is_none(self):
        data = _valuation_percentile_rows([CompanyRow("a", "A", pe=10.0)])
        self.assertIsNone(data[0]["pe_cheap_pct"])

    def test_valuation_percentile_excludes_non_positive_pe(self):
        rows = [
            CompanyRow("loss", "亏损", pe=-5.0),
            CompanyRow("a", "A", pe=10.0),
            CompanyRow("b", "B", pe=20.0),
        ]
        data = {d["symbol"]: d for d in _valuation_percentile_rows(rows)}
        self.assertIsNone(data["loss"]["pe_cheap_pct"])
        self.assertEqual(data["a"]["pe_cheap_pct"], 100.0)

    def test_valuation_figure_json_structure(self):
        rows = [
            CompanyRow("a", "A", pe=10.0, pb=1.0),
            CompanyRow("b", "B", pe=20.0, pb=2.0),
        ]
        fig = valuation_figure_json(rows)
        if fig is None:
            self.skipTest("plotly 未安装")
        self.assertIn("data", fig)
        self.assertGreaterEqual(len(fig["data"]), 1)

    def test_valuation_figure_json_none_for_single_company(self):
        fig = valuation_figure_json([CompanyRow("a", "A", pe=10.0)])
        self.assertIsNone(fig)


# ===========================================================================
# sentiment.py
# ===========================================================================
class _FakeLLM:
    def __init__(self, result):
        self._result = result

    def chat_json(self, messages, **kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


_NEWS = [
    {"title": "公司发布业绩快报", "content": "营收增长 12.5%"},
    {"title": "行业景气度回升"},
    {"title": "原材料价格波动"},
]


class SentimentTest(unittest.TestCase):
    def test_empty_news_returns_fixed_neutral_structure(self):
        result = analyze_sentiment([], llm=_FakeLLM({}))
        self.assertEqual(result["overall"], "中性")
        self.assertEqual(result["score"], 50.0)
        self.assertEqual(result["positive"], 0)
        self.assertEqual(result["negative"], 0)
        self.assertEqual(result["neutral"], 0)
        self.assertEqual(result["positive_news"], [])
        self.assertEqual(result["negative_news"], [])
        self.assertFalse(result["analyzed"])
        for key in ("overall", "score", "positive", "negative", "neutral",
                    "positive_ratio", "negative_ratio", "neutral_ratio",
                    "positive_news", "negative_news", "summary"):
            self.assertIn(key, result)

    def test_offline_exception_does_not_propagate(self):
        result = analyze_sentiment(_NEWS, llm=_FakeLLM(RuntimeError("offline")))
        self.assertFalse(result["analyzed"])
        self.assertEqual(result["overall"], "中性")
        self.assertEqual(result["score"], 50.0)
        self.assertEqual(result["news_count"], len(_NEWS))
        self.assertIn("失败", result["summary"])

    def test_non_dict_llm_result_falls_back(self):
        result = analyze_sentiment(_NEWS, llm=_FakeLLM("这是一段非结构化文本"))
        self.assertFalse(result["analyzed"])
        self.assertEqual(result["overall"], "中性")
        self.assertEqual(result["news_count"], len(_NEWS))

    def test_dirty_counts_and_score_are_coerced(self):
        parsed = {
            "positive": "3条",
            "negative": -2,
            "neutral": "abc",
            "score": "72分",
            "summary": "整体偏正面",
        }
        result = analyze_sentiment(_NEWS, llm=_FakeLLM(parsed))
        self.assertTrue(result["analyzed"])
        self.assertEqual(result["positive"], 3)
        self.assertEqual(result["negative"], 0)   # 负数被夹到 0
        self.assertEqual(result["neutral"], 0)    # 非数字退化为 0
        self.assertEqual(result["score"], 72.0)
        self.assertEqual(result["overall"], "正面")
        self.assertEqual(result["summary"], "整体偏正面")
        self.assertAlmostEqual(
            result["positive_ratio"] + result["negative_ratio"] + result["neutral_ratio"],
            1.0, places=3,
        )

    def test_zero_counts_fall_back_to_neutral(self):
        parsed = {"positive": 0, "negative": 0, "neutral": 0, "score": "50"}
        result = analyze_sentiment(_NEWS, llm=_FakeLLM(parsed))
        self.assertEqual(result["neutral"], len(_NEWS))
        self.assertEqual(result["neutral_ratio"], 1.0)
        self.assertEqual(result["positive_ratio"], 0.0)

    def test_score_is_clamped_0_to_100(self):
        high = analyze_sentiment(_NEWS, llm=_FakeLLM(
            {"positive": 3, "negative": 0, "neutral": 0, "score": "150分"}))
        self.assertEqual(high["score"], 100.0)
        low = analyze_sentiment(_NEWS, llm=_FakeLLM(
            {"positive": 0, "negative": 3, "neutral": 0, "score": "-5"}))
        self.assertEqual(low["score"], 0.0)
        self.assertEqual(low["overall"], "负面")

    def test_overall_uses_counts_when_score_neutral(self):
        result = analyze_sentiment(_NEWS, llm=_FakeLLM(
            {"positive": 0, "negative": 2, "neutral": 1, "score": 50}))
        self.assertEqual(result["overall"], "负面")

    def test_positive_and_negative_news_lists_are_normalized(self):
        parsed = {
            "positive": 1, "negative": 1, "neutral": 0, "score": 50,
            "positive_news": [{"title": "利好", "reason": "订单增长"}],
            "negative_news": ["纯字符串风险项"],
        }
        result = analyze_sentiment(_NEWS, llm=_FakeLLM(parsed))
        self.assertEqual(result["positive_news"][0]["title"], "利好")
        self.assertEqual(result["negative_news"][0]["title"], "纯字符串风险项")
        self.assertEqual(result["negative_news"][0]["reason"], "")

    def test_non_dict_news_items_are_ignored(self):
        news = [{"title": "正常新闻"}, "脏数据", 123, None]
        result = analyze_sentiment(news, llm=_FakeLLM({"score": 60}))
        self.assertTrue(result["analyzed"])
        self.assertEqual(result["news_count"], 1)

    def test_all_dirty_news_returns_honest_empty_result(self):
        result = analyze_sentiment(["脏", 1, None], llm=_FakeLLM({}))
        self.assertFalse(result["analyzed"])
        self.assertIn("格式异常", result["summary"])


# ===========================================================================
# indicators.py
# ===========================================================================
class IndicatorsTest(unittest.TestCase):
    def test_analyze_technicals_requires_35_bars(self):
        for n in (0, 10, 34):
            df = _price_df(list(np.linspace(10, 20, n)))
            self.assertEqual(indicators.analyze_technicals(df), {})
        self.assertEqual(indicators.analyze_technicals(None), {})

    def test_analyze_technicals_returns_full_structure_at_60_bars(self):
        df = _price_df(list(np.linspace(10, 30, 60)))
        result = indicators.analyze_technicals(df)
        for key in ("trend", "signals", "macd", "rsi", "kdj", "boll", "ma", "close"):
            self.assertIn(key, result)
        for key in ("dif", "dea", "hist"):
            self.assertIn(key, result["macd"])
        for key in ("k", "d", "j"):
            self.assertIn(key, result["kdj"])
        for key in ("upper", "mid", "lower"):
            self.assertIn(key, result["boll"])
        self.assertIn(result["trend"], (
            "多头排列（短期均线向上）", "空头排列（短期均线向下）", "震荡"))

    def test_rsi_all_up_is_100(self):
        """回归：单边上涨时 RSI 必须为 100，而不是被 fillna(50) 误报成中性。"""
        close = pd.Series(np.arange(1, 60, dtype=float))
        rsi = indicators.calc_rsi(close)
        self.assertAlmostEqual(float(rsi.iloc[-1]), 100.0, places=6)
        self.assertNotAlmostEqual(float(rsi.iloc[-1]), 50.0, places=6)

    def test_rsi_all_down_is_zero(self):
        close = pd.Series(np.arange(60, 1, -1, dtype=float))
        rsi = indicators.calc_rsi(close)
        self.assertAlmostEqual(float(rsi.iloc[-1]), 0.0, places=6)

    def test_rsi_warmup_filled_with_neutral(self):
        close = pd.Series(np.arange(1, 60, dtype=float))
        rsi = indicators.calc_rsi(close, period=14)
        self.assertTrue(rsi.iloc[:13].eq(50).all())
        self.assertTrue(rsi.notna().all())

    def test_rsi_high_in_monotonic_up_signals(self):
        df = _price_df(list(np.linspace(10, 40, 60)))
        result = indicators.analyze_technicals(df)
        self.assertEqual(result["rsi"], 100.0)
        self.assertTrue(any("超买" in s for s in result["signals"]))

    def test_macd_uses_12_26_9_parameters(self):
        close = pd.Series(np.linspace(10, 40, 80) + np.sin(np.arange(80)))
        dif, dea, hist = indicators.calc_macd(close)
        exp_fast = close.ewm(span=12, adjust=False).mean()
        exp_slow = close.ewm(span=26, adjust=False).mean()
        exp_dif = exp_fast - exp_slow
        exp_dea = exp_dif.ewm(span=9, adjust=False).mean()
        pd.testing.assert_series_equal(dif, exp_dif)
        pd.testing.assert_series_equal(dea, exp_dea)
        pd.testing.assert_series_equal(hist, (exp_dif - exp_dea) * 2)

    def test_boll_uses_population_std(self):
        close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        upper, mid, lower = indicators.calc_boll(close, n=5, k=2)
        # 总体标准差 ddof=0 → sqrt(2) ≈ 1.4142
        self.assertAlmostEqual(float(mid.iloc[-1]), 3.0, places=6)
        self.assertAlmostEqual(float(upper.iloc[-1]), 3 + 2 * math.sqrt(2), places=4)
        self.assertAlmostEqual(float(lower.iloc[-1]), 3 - 2 * math.sqrt(2), places=4)

    def test_boll_flat_series_has_zero_width(self):
        close = pd.Series([7.0] * 25)
        upper, mid, lower = indicators.calc_boll(close, n=20, k=2)
        self.assertEqual(float(upper.iloc[-1]), 7.0)
        self.assertEqual(float(lower.iloc[-1]), 7.0)
        self.assertEqual(float(mid.iloc[-1]), 7.0)

    def test_kdj_relationship_j_equals_3k_minus_2d(self):
        df = _price_df(list(np.linspace(10, 30, 60)))
        result = indicators.analyze_technicals(df)
        k, d, j = result["kdj"]["k"], result["kdj"]["d"], result["kdj"]["j"]
        self.assertAlmostEqual(j, 3 * k - 2 * d, places=0)

    def test_monotonic_up_reports_no_double_bottom(self):
        """回归：单调上涨不得再被误报为『双底』。"""
        closes = list(np.linspace(10, 50, 60))
        result = indicators.analyze_technicals(_price_df(closes))
        self.assertFalse(any("双底" in s for s in result["signals"]),
                         f"单调上涨误报双底：{result['signals']}")
        self.assertFalse(any("双顶" in s for s in result["signals"]))

    def test_monotonic_down_reports_no_double_top(self):
        """回归：单调下跌不得再被误报为『双顶』。"""
        closes = list(np.linspace(50, 10, 60))
        result = indicators.analyze_technicals(_price_df(closes))
        self.assertFalse(any("双顶" in s for s in result["signals"]),
                         f"单调下跌误报双顶：{result['signals']}")
        self.assertFalse(any("双底" in s for s in result["signals"]))

    def test_real_double_top_is_still_detected(self):
        """修复不能把真实形态一起干掉：构造清晰的 M 头应能识别。"""
        # 前 29 根走平，随后 20 根构成两个分离的高点 + 中间明显回落
        shape = [15, 16, 17, 18, 19, 20,
                 17, 16, 15,
                 16, 17, 18, 19, 20,
                 17, 16.5, 16, 15.5, 15, 15]
        closes = [15.0] * 29 + [float(v) for v in shape] + [15.0]
        result = indicators.analyze_technicals(_price_df(closes))
        self.assertTrue(any("双顶" in s for s in result["signals"]),
                        f"真实双顶漏报：{result['signals']}")

    def test_trend_is_bullish_for_rising_sequence(self):
        df = _price_df(list(np.linspace(10, 40, 70)))
        result = indicators.analyze_technicals(df)
        self.assertEqual(result["trend"], "多头排列（短期均线向上）")
        self.assertIsNotNone(result["ma"]["ma60"])


# ===========================================================================
# chain.py
# ===========================================================================
class ChainTest(unittest.TestCase):
    def test_chain_db_has_14_chains(self):
        self.assertEqual(len(CHAIN_DB), 14)
        self.assertEqual(len(list_chains()), 14)
        self.assertEqual(set(list_chains()), set(CHAIN_DB.keys()))

    def test_normalize_code_variants(self):
        for raw in ("600519", "600519.SH", "sh600519", "SH600519"):
            self.assertEqual(_normalize_code(raw), "600519", raw)
        self.assertEqual(_normalize_code("2382.HK"), "2382")
        self.assertEqual(_normalize_code("aapl"), "AAPL")

    def test_find_chain_normalizes_symbol(self):
        expected_code = "600519"
        for raw in ("600519", "600519.SH", "sh600519"):
            info = find_chain(raw)
            self.assertIsNotNone(info, raw)
            self.assertEqual(info["chain"], "白酒")
            self.assertEqual(info["position"], "upstream")
            codes = [c["code"] for c in info["upstream"]]
            self.assertIn(expected_code, codes)
            names = [c["name"] for c in info["upstream"]]
            self.assertIn("贵州茅台", names)

    def test_find_chain_by_name(self):
        info = find_chain("", "贵州茅台")
        self.assertIsNotNone(info)
        self.assertEqual(info["chain"], "白酒")

    def test_find_chain_hk_and_us_codes(self):
        self.assertEqual(find_chain("2382.HK")["chain"], "消费电子")
        self.assertEqual(find_chain("AAPL")["chain"], "消费电子")

    def test_find_chain_unknown_returns_none(self):
        self.assertIsNone(find_chain("999999"))
        self.assertIsNone(find_chain("", "不存在的公司"))

    def test_chain_to_text_renders_layers(self):
        info = find_chain("600519.SH")
        text = chain_to_text(info)
        self.assertIn("白酒产业链", text)
        self.assertIn("上游", text)
        self.assertIn("贵州茅台", text)
        self.assertIn("600519", text)
        self.assertEqual(chain_to_text(None), "")


class ChainGraphHtmlTest(unittest.TestCase):
    """chain_graph_html 的回归测试。

    曾经用了新版 plotly 已移除的 ``layout.titlefont_size``，
    导致**有产业链的标的（如 600519）在生成公司速览时直接抛 ValueError**。
    """

    def test_chain_graph_renders_for_real_symbol(self):
        from src.analysis.chain_viz import chain_graph_html
        info = find_chain("600519", "贵州茅台")
        self.assertIsNotNone(info)
        html = chain_graph_html(info, "600519")
        self.assertTrue(html)
        self.assertIn("plotly", html.lower())

    def test_chain_graph_returns_empty_without_chain(self):
        from src.analysis.chain_viz import chain_graph_html
        self.assertEqual(chain_graph_html(None, "000000"), "")


if __name__ == "__main__":
    unittest.main()
