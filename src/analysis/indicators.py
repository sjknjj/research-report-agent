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
