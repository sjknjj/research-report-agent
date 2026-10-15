"""简单回测（简化版）。

用 MACD 金叉死叉策略回测历史数据，看胜率。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd

from .indicators import calc_macd

logger = logging.getLogger("analysis.backtest")


def backtest_macd(price_df: pd.DataFrame, initial_capital: float = 100000) -> Dict[str, Any]:
    """MACD 金叉死叉策略回测。

    策略：金叉买入，死叉卖出。
    """
    if price_df is None or len(price_df) < 30:
        return {"error": "数据不足，无法回测"}

    close = price_df["close"]
    dif, dea, macd = calc_macd(close)

    # 找金叉死叉点
    positions = []
    in_position = False
    buy_price = 0

    for i in range(1, len(close)):
        # 金叉：DIF 上穿 DEA
        if dif.iloc[i-1] < dea.iloc[i-1] and dif.iloc[i] > dea.iloc[i] and not in_position:
            buy_price = close.iloc[i]
            in_position = True
            positions.append({"date": price_df.index[i], "type": "buy", "price": buy_price})
        # 死叉：DIF 下穿 DEA
        elif dif.iloc[i-1] > dea.iloc[i-1] and dif.iloc[i] < dea.iloc[i] and in_position:
            sell_price = close.iloc[i]
            profit = (sell_price - buy_price) / buy_price * 100
            positions.append({"date": price_df.index[i], "type": "sell", "price": sell_price, "profit_pct": profit})
            in_position = False

    # 统计
    sell_positions = [p for p in positions if p["type"] == "sell"]
    if not sell_positions:
        return {"total_trades": 0, "win_rate": 0, "avg_profit": 0, "total_return": 0}

    wins = [p for p in sell_positions if p["profit_pct"] > 0]
    win_rate = len(wins) / len(sell_positions) * 100
    avg_profit = sum(p["profit_pct"] for p in sell_positions) / len(sell_positions)
    total_return = (close.iloc[-1] - close.iloc[0]) / close.iloc[0] * 100

    return {
        "total_trades": len(sell_positions),
        "win_rate": round(win_rate, 1),
        "avg_profit": round(avg_profit, 2),
        "total_return": round(total_return, 2),
        "positions": positions[-10:],  # 最近 10 次交易
    }
