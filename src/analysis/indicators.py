import numpy as np
import pandas as pd


def calc_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd = (dif - dea) * 2
    return dif, dea, macd


def calc_rsi(close: pd.Series, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series, n=9):
    lowest_low = low.rolling(n, min_periods=1).min()
    highest_high = high.rolling(n, min_periods=1).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def calc_boll(close: pd.Series, n=20, k=2):
    mid = close.rolling(n, min_periods=1).mean()
    std = close.rolling(n, min_periods=1).std()
    upper = mid + k * std
    lower = mid - k * std
    return upper, mid, lower


def analyze_technicals(price_df: pd.DataFrame) -> dict:
    if price_df is None or len(price_df) < 30:
        return {}
    close = price_df["close"]
    high = price_df["high"] if "high" in price_df else close
    low = price_df["low"] if "low" in price_df else close

    dif, dea, macd_hist = calc_macd(close)
    rsi = calc_rsi(close)
    k, d, j = calc_kdj(high, low, close)
    boll_up, boll_mid, boll_low = calc_boll(close)

    last = len(close) - 1
    signals = []

    if dif.iloc[last] > dea.iloc[last] and dif.iloc[last-1] <= dea.iloc[last-1]:
        signals.append("MACD金叉（短期均线上穿长期均线，偏多）")
    elif dif.iloc[last] < dea.iloc[last] and dif.iloc[last-1] >= dea.iloc[last-1]:
        signals.append("MACD死叉（短期均线下穿长期均线，偏空）")

    rsi_val = rsi.iloc[last]
    if rsi_val > 70:
        signals.append(f"RSI={rsi_val:.1f}，超买区域，注意回调风险")
    elif rsi_val < 30:
        signals.append(f"RSI={rsi_val:.1f}，超卖区域，可能存在反弹机会")

    k_val, d_val, j_val = k.iloc[last], d.iloc[last], j.iloc[last]
    if k_val > d_val and k.iloc[last-1] <= d.iloc[last-1]:
        signals.append("KDJ金叉，短期买入信号")
    elif k_val < d_val and k.iloc[last-1] >= d.iloc[last-1]:
        signals.append("KDJ死叉，短期卖出信号")
    if j_val > 100:
        signals.append(f"J值={j_val:.1f}，严重超买")
    elif j_val < 0:
        signals.append(f"J值={j_val:.1f}，严重超卖")

    price_now = close.iloc[last]
    upper, lower = boll_up.iloc[last], boll_low.iloc[last]
    if price_now > upper:
        signals.append(f"股价突破布林带上轨（{upper:.2f}），短期强势但乖离偏大")
    elif price_now < lower:
        signals.append(f"股价跌破布林带下轨（{lower:.2f}），短期弱势")

    ma5 = close.rolling(5, min_periods=1).mean().iloc[last]
    ma20 = close.rolling(20, min_periods=1).mean().iloc[last]
    ma60 = close.rolling(60, min_periods=1).mean().iloc[last] if len(close) >= 60 else None

    trend = "震荡"
    if ma5 > ma20 and (ma60 is None or ma20 > ma60):
        trend = "多头排列（短期均线向上）"
    elif ma5 < ma20 and (ma60 is None or ma20 < ma60):
        trend = "空头排列（短期均线向下）"

    # 3.4：K线形态识别
    patterns = _detect_candlestick_patterns(price_df, last)
    if patterns:
        signals.extend(patterns)

    return {
        "trend": trend,
        "signals": signals,
        "macd": {"dif": round(float(dif.iloc[last]), 3),
                  "dea": round(float(dea.iloc[last]), 3),
                  "hist": round(float(macd_hist.iloc[last]), 3)},
        "rsi": round(float(rsi_val), 1),
        "kdj": {"k": round(float(k_val), 1),
                "d": round(float(d_val), 1),
                "j": round(float(j_val), 1)},
        "boll": {"upper": round(float(upper), 2),
                 "mid": round(float(boll_mid.iloc[last]), 2),
                 "lower": round(float(lower), 2)},
        "ma": {"ma5": round(float(ma5), 2),
               "ma20": round(float(ma20), 2),
               "ma60": round(float(ma60), 2) if ma60 else None},
        "close": round(float(price_now), 2),
    }


def _detect_candlestick_patterns(price_df: pd.DataFrame, last: int) -> list:
    """3.4：简单 K 线形态识别（十字星/大阳大阴/双底双顶）。"""
    patterns = []
    if last < 5:
        return patterns

    open_ = price_df["open"] if "open" in price_df else price_df["close"]
    close = price_df["close"]
    high = price_df["high"] if "high" in price_df else close
    low = price_df["low"] if "low" in price_df else close

    # 1. 十字星（开盘≈收盘，振幅不大）
    body = abs(close.iloc[last] - open_.iloc[last])
    range_ = high.iloc[last] - low.iloc[last]
    if range_ > 0 and body / range_ < 0.1 and range_ / close.iloc[last] > 0.01:
        patterns.append("十字星形态（多空分歧，可能变盘）")

    # 2. 大阳线/大阴线
    pct = (close.iloc[last] - open_.iloc[last]) / open_.iloc[last] * 100
    if pct > 3:
        patterns.append(f"大阳线（涨幅 {pct:.1f}%，多头强势）")
    elif pct < -3:
        patterns.append(f"大阴线（跌幅 {abs(pct):.1f}%，空头强势）")

    # 3. 双底（W底）— 最近 20 天内有两个相近的低点
    if last >= 20:
        recent_low = low.iloc[last-20:last]
        min1 = recent_low.min()
        min1_idx = recent_low.idxmin()
        # 找第二个低点（在第一个低点之后）
        after_first = low.iloc[last-20:last][min1_idx:]
        if len(after_first) > 3:
            min2 = after_first.min()
            if abs(min1 - min2) / min1 < 0.03 and min2 > min1 * 0.98:
                patterns.append("疑似双底（W底）形态，可能见底反转")

    # 4. 双顶（M头）— 最近 20 天内有两个相近的高点
    if last >= 20:
        recent_high = high.iloc[last-20:last]
        max1 = recent_high.max()
        max1_idx = recent_high.idxmax()
        after_first = high.iloc[last-20:last][max1_idx:]
        if len(after_first) > 3:
            max2 = after_first.max()
            if abs(max1 - max2) / max1 < 0.03 and max2 < max1 * 1.02:
                patterns.append("疑似双顶（M头）形态，可能见顶回落")

    # 5. 看涨吞没（前一天阴线，今天大阳线完全包住昨天）
    if last >= 1:
        prev_close = close.iloc[last-1]
        prev_open = open_.iloc[last-1]
        curr_close = close.iloc[last]
        curr_open = open_.iloc[last]
        if prev_close < prev_open and curr_close > curr_open:
            if curr_open <= prev_close and curr_close >= prev_open:
                patterns.append("看涨吞没形态，可能见底反转")

    # 6. 看跌吞没（前一天阳线，今天大阴线完全包住昨天）
    if last >= 1:
        prev_close = close.iloc[last-1]
        prev_open = open_.iloc[last-1]
        curr_close = close.iloc[last]
        curr_open = open_.iloc[last]
        if prev_close > prev_open and curr_close < curr_open:
            if curr_open >= prev_close and curr_close <= prev_open:
                patterns.append("看跌吞没形态，可能见顶回落")

    # 7. 早晨之星（三根K线：大阴线 + 小十字星 + 大阳线）
    if last >= 2:
        c1_close = close.iloc[last-2]
        c1_open = open_.iloc[last-2]
        c2_close = close.iloc[last-1]
        c2_open = open_.iloc[last-1]
        c3_close = close.iloc[last]
        c3_open = open_.iloc[last]
        body1 = abs(c1_close - c1_open) / c1_open
        body2 = abs(c2_close - c2_open) / c2_open
        body3 = abs(c3_close - c3_open) / c3_open
        if body1 > 0.02 and body2 < 0.01 and body3 > 0.02:
            if c1_close < c1_open and c3_close > c3_open:
                if c2_close < c1_open and c3_close > (c1_open + c1_close) / 2:
                    patterns.append("早晨之星形态，可能见底反转")

    # 8. 黄昏之星（三根K线：大阳线 + 小十字星 + 大阴线）
    if last >= 2:
        c1_close = close.iloc[last-2]
        c1_open = open_.iloc[last-2]
        c2_close = close.iloc[last-1]
        c2_open = open_.iloc[last-1]
        c3_close = close.iloc[last]
        c3_open = open_.iloc[last]
        body1 = abs(c1_close - c1_open) / c1_open
        body2 = abs(c2_close - c2_open) / c2_open
        body3 = abs(c3_close - c3_open) / c3_open
        if body1 > 0.02 and body2 < 0.01 and body3 > 0.02:
            if c1_close > c1_open and c3_close < c3_open:
                if c2_close > c1_open and c3_close < (c1_open + c1_close) / 2:
                    patterns.append("黄昏之星形态，可能见顶回落")

    return patterns
