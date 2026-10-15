"""MACD 金叉/死叉策略回测。

实现要点（与上层 Web/CLI 的接口契约见 FIX_CONTRACTS）：
1. **消除前视**：信号在 T 日收盘后才确认，因此统一在 **T+1 开盘**成交
   （没有开盘价时退化为 T+1 收盘）。
2. **计入交易成本**：买入佣金万 2.5，卖出佣金万 2.5 + 印花税千 1。
3. **期末强制平仓**：回测结束时仍未卖出的持仓按最后收盘价平掉，计入胜率。
4. **所有比率为百分数（0-100）**：调用方不要再 ×100。
5. 返回的 `positions` 全部为可 JSON 序列化的原生类型（日期为字符串）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List

import pandas as pd

from .indicators import calc_macd

logger = logging.getLogger("analysis.backtest")

# 交易成本（单边）
FEE_BUY = 0.00025                 # 佣金 万 2.5
FEE_SELL = 0.00025 + 0.001        # 佣金 万 2.5 + 印花税 千 1

_MIN_BARS = 30


def _empty_result(reason: str) -> Dict[str, Any]:
    """数据不足等情况下的统一空结果（保持键结构一致）。"""
    return {
        "error": reason,
        "total_trades": 0,
        "win_trades": 0,
        "lose_trades": 0,
        "win_rate": 0.0,
        "avg_profit_pct": 0.0,
        "strategy_return_pct": 0.0,
        "buy_hold_return_pct": 0.0,
        "total_return": 0.0,
        "max_drawdown_pct": 0.0,
        "summary": reason,
        "positions": [],
    }


def _date_str(value) -> str:
    """把 Timestamp/datetime 转成字符串，保证可 JSON 序列化。"""
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    return str(value)


def _max_drawdown_pct(equity: List[float]) -> float:
    """最大回撤（百分数，<= 0）。"""
    if not equity:
        return 0.0
    peak = equity[0]
    worst = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (value - peak) / peak * 100
            if drawdown < worst:
                worst = drawdown
    return round(worst, 2)


def backtest_macd(
    price_df: pd.DataFrame, initial_capital: float = 100000
) -> Dict[str, Any]:
    """MACD 金叉死叉策略回测。

    :param price_df: 需包含 `close` 列；有 `open` 时按 T+1 开盘成交，否则按收盘。
                     有 `date` 列则用作成交日期。
    :param initial_capital: 初始资金（元），用于计算净值曲线与收益率。
    """
    if price_df is None or len(price_df) < _MIN_BARS:
        return _empty_result("数据不足，无法回测（至少需要 30 个交易日）")
    if "close" not in price_df.columns:
        return _empty_result("行情数据缺少 close 列，无法回测")

    df = price_df.reset_index(drop=True)
    close = pd.to_numeric(df["close"], errors="coerce")
    if close.isna().all():
        return _empty_result("收盘价全部缺失，无法回测")

    if "open" in df.columns:
        exec_price = pd.to_numeric(df["open"], errors="coerce")
        exec_price = exec_price.where(exec_price.notna() & (exec_price > 0), close)
    else:
        exec_price = close

    if "date" in df.columns:
        labels = df["date"]
    else:
        labels = pd.Series(df.index)

    dif, dea, _ = calc_macd(close)

    cash = float(initial_capital)
    shares = 0.0
    buy_price = None
    pending = None                       # None / "buy" / "sell"
    trades: List[Dict[str, Any]] = []
    equity: List[float] = [float(initial_capital)]

    for i in range(1, len(df)):
        # ---- 1) 先执行上一根 K 线收盘后确认的信号（T+1 成交）----
        if pending == "buy" and shares == 0:
            px = float(exec_price.iloc[i])
            if px > 0:
                shares = cash * (1 - FEE_BUY) / px
                cash = 0.0
                buy_price = px
                trades.append({
                    "date": _date_str(labels.iloc[i]),
                    "type": "buy",
                    "price": round(px, 3),
                })
        elif pending == "sell" and shares > 0:
            px = float(exec_price.iloc[i])
            if px > 0:
                cash = shares * px * (1 - FEE_SELL)
                profit_pct = (px * (1 - FEE_SELL) - buy_price) / buy_price * 100
                trades.append({
                    "date": _date_str(labels.iloc[i]),
                    "type": "sell",
                    "price": round(px, 3),
                    "profit_pct": round(profit_pct, 2),
                })
                shares = 0.0
                buy_price = None
        pending = None

        # ---- 2) 期末净值（按当日收盘价估值）----
        equity.append(cash + shares * float(close.iloc[i]))

        # ---- 3) 当日收盘后确认信号，留待下一根 K 线成交 ----
        if dif.iloc[i - 1] < dea.iloc[i - 1] and dif.iloc[i] > dea.iloc[i] and shares == 0:
            pending = "buy"
        elif dif.iloc[i - 1] > dea.iloc[i - 1] and dif.iloc[i] < dea.iloc[i] and shares > 0:
            pending = "sell"

    # ---- 期末未平仓：按最后收盘价强制平仓 ----
    if shares > 0 and buy_price:
        px = float(close.iloc[-1])
        cash = shares * px * (1 - FEE_SELL)
        profit_pct = (px * (1 - FEE_SELL) - buy_price) / buy_price * 100
        trades.append({
            "date": _date_str(labels.iloc[-1]),
            "type": "sell",
            "price": round(px, 3),
            "profit_pct": round(profit_pct, 2),
            "forced_close": True,
        })
        shares = 0.0
        equity[-1] = cash

    sells = [t for t in trades if t["type"] == "sell"]
    wins = [t for t in sells if t.get("profit_pct", 0) > 0]
    loses = [t for t in sells if t.get("profit_pct", 0) <= 0]

    strategy_return_pct = (cash / float(initial_capital) - 1) * 100 if initial_capital else 0.0
    first_close = float(close.iloc[0])
    last_close = float(close.iloc[-1])
    buy_hold_return_pct = (
        (last_close / first_close - 1) * 100 if first_close > 0 else 0.0
    )

    win_rate = (len(wins) / len(sells) * 100) if sells else 0.0
    avg_profit = (sum(t["profit_pct"] for t in sells) / len(sells)) if sells else 0.0
    max_dd = _max_drawdown_pct(equity)

    if not sells:
        summary = "回测区间内未产生完整的金叉→死叉交易，策略未建仓或未平仓。"
    else:
        summary = (
            f"回测共完成 {len(sells)} 笔交易，胜率 {win_rate:.1f}%，"
            f"策略累计收益 {strategy_return_pct:.2f}%（同期买入持有 {buy_hold_return_pct:.2f}%），"
            f"最大回撤 {max_dd:.2f}%（已计入佣金与印花税）。"
        )

    return {
        "total_trades": len(sells),
        "win_trades": len(wins),
        "lose_trades": len(loses),
        "win_rate": round(win_rate, 1),
        "avg_profit_pct": round(avg_profit, 2),
        "strategy_return_pct": round(strategy_return_pct, 2),
        "buy_hold_return_pct": round(buy_hold_return_pct, 2),
        # 兼容旧字段名：含义是策略累计收益率
        "total_return": round(strategy_return_pct, 2),
        "max_drawdown_pct": max_dd,
        "summary": summary,
        "positions": trades[-10:],
        "fees": {"buy": FEE_BUY, "sell": FEE_SELL},
    }
