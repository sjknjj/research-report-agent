"""A 股数据清洗模块。

AKShare 返回的原始 DataFrame 存在大量需要清洗的“脏格式”：
- 列名为中文，且不同接口命名不一致；
- 金额带中文单位（如 ``1.47亿`` / ``2.50万``）；
- 增长率 / 比率带百分号（如 ``46.84%``）；
- 缺失值用布尔 ``False``、字符串 ``"--"`` 等混合表示；
- 日期为字符串，数值列为 object。

本模块把它们统一转换为：英文蛇形列名 + 正确的数值/日期类型 + 去重排序，
并进一步计算行情与财务的派生统计，供投研助手与交叉校验使用。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 基础解析
# ---------------------------------------------------------------------------
_MISSING = {"false", "--", "-", "", "nan", "none", "null", "不适用", "False"}
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_cn_number(value: Any) -> float:
    """把带中文单位 / 百分号 / 缺失标记的值解析为 float，无法解析返回 NaN。

    金额：``亿`` ×1e8、``万`` ×1e4、``万亿`` ×1e12（单位：元）；
    百分比：去掉 ``%`` 后按“百分数数值”返回（如 46.84 -> 46.84，而非 0.4684）。
    """
    if value is None or isinstance(value, bool):
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)

    text = str(value).strip().replace(",", "").replace("，", "")
    if text in _MISSING or text.lower() in _MISSING:
        return np.nan

    is_percent = "%" in text
    multiplier = 1.0
    if "万亿" in text:
        multiplier = 1e12
    elif "亿" in text:
        multiplier = 1e8
    elif "万" in text:
        multiplier = 1e4

    match = _NUM_RE.search(text.replace(" ", ""))
    if not match:
        return np.nan
    number = float(match.group()) * multiplier
    return number  # 百分号不在此处理，由 parse_percent 语义化处理


def parse_percent(value: Any) -> float:
    """把百分比字符串解析为百分数数值（``46.84%`` -> 46.84），缺失返回 NaN。"""
    if value is None or isinstance(value, bool):
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in _MISSING or text.lower() in _MISSING:
        return np.nan
    match = _NUM_RE.search(text.replace(" ", ""))
    return float(match.group()) if match else np.nan


def _safe_round(value: Any, ndigits: int = 4) -> Optional[float]:
    """转为原生 float 并四舍五入，NaN/inf 统一为 None（便于 JSON 序列化）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, ndigits)


# ---------------------------------------------------------------------------
# 行情清洗
# ---------------------------------------------------------------------------
PRICE_COLUMN_MAP = {
    "日期": "date",
    "股票代码": "symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude_pct",
    "涨跌幅": "pct_change",
    "涨跌额": "change",
    "换手率": "turnover_pct",
}
PRICE_NUMERIC = [
    "open", "close", "high", "low", "volume", "amount",
    "amplitude_pct", "pct_change", "change", "turnover_pct",
]


def clean_price(df: pd.DataFrame) -> pd.DataFrame:
    """清洗日线行情：列名标准化、类型转换、去重排序、派生均线与日收益。"""
    if df is None or df.empty:
        return pd.DataFrame()
    data = df.copy()
    data.columns = [str(c).strip() for c in data.columns]
    data = data.rename(columns={k: v for k, v in PRICE_COLUMN_MAP.items() if k in data.columns})

    if "date" not in data:
        raise ValueError("行情数据缺少『日期』列")
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    for col in PRICE_NUMERIC:
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce")

    data = (
        data.dropna(subset=["date", "close"])
        .drop_duplicates(subset=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    # 派生指标
    data["daily_ret"] = data["close"].pct_change()
    data["ma5"] = data["close"].rolling(5, min_periods=1).mean()
    data["ma20"] = data["close"].rolling(20, min_periods=1).mean()
    return data


def summarize_price(clean: pd.DataFrame) -> Dict[str, Any]:
    """从清洗后的行情计算区间统计快照（原生类型，可直接 JSON 化）。"""
    if clean is None or clean.empty:
        return {}
    latest = clean.iloc[-1]
    first_close = float(clean["close"].iloc[0])
    last_close = float(clean["close"].iloc[-1])
    period_return = (last_close / first_close - 1) * 100 if first_close else np.nan
    annual_vol = clean["daily_ret"].std() * math.sqrt(244) * 100

    summary = {
        "trading_days": int(len(clean)),
        "start_date": clean["date"].iloc[0].strftime("%Y-%m-%d"),
        "end_date": clean["date"].iloc[-1].strftime("%Y-%m-%d"),
        "latest_close": _safe_round(latest.get("close"), 2),
        "latest_pct_change": _safe_round(latest.get("pct_change"), 2),
        "period_return_pct": _safe_round(period_return, 2),
        "period_high": _safe_round(clean["high"].max() if "high" in clean else last_close, 2),
        "period_low": _safe_round(clean["low"].min() if "low" in clean else last_close, 2),
        "avg_turnover_pct": _safe_round(clean["turnover_pct"].mean() if "turnover_pct" in clean else np.nan, 3),
        "annual_volatility_pct": _safe_round(annual_vol, 2),
        "ma5": _safe_round(latest.get("ma5"), 2),
        "ma20": _safe_round(latest.get("ma20"), 2),
        "total_amount_yuan": _safe_round(clean["amount"].sum() if "amount" in clean else np.nan, 0),
    }
    ma20 = summary.get("ma20")
    if ma20 and summary["latest_close"]:
        summary["price_vs_ma20"] = "站上20日均线" if summary["latest_close"] >= ma20 else "跌破20日均线"
    else:
        summary["price_vs_ma20"] = None
    return summary


# ---------------------------------------------------------------------------
# 财务清洗（同花顺财务摘要口径）
# ---------------------------------------------------------------------------
FINANCIAL_COLUMN_MAP = {
    "报告期": "period",
    "营业总收入": "revenue",
    "营业总收入同比增长率": "revenue_yoy",
    "营业收入": "revenue",
    "营业收入同比增长率": "revenue_yoy",
    "净利润": "net_profit",
    "净利润同比增长率": "net_profit_yoy",
    "归母净利润": "net_profit",
    "扣非净利润": "net_profit_deducted",
    "扣非净利润同比增长率": "net_profit_deducted_yoy",
    "基本每股收益": "eps",
    "每股净资产": "bps",
    "每股经营现金流": "ocf_ps",
    "销售毛利率": "gross_margin",
    "销售净利率": "net_margin",
    "净资产收益率": "roe",
    "净资产收益率-摊薄": "roe_diluted",
    "资产负债率": "debt_ratio",
}
_AMOUNT_FIELDS = ["revenue", "net_profit", "net_profit_deducted"]
_PERCENT_FIELDS = [
    "revenue_yoy", "net_profit_yoy", "net_profit_deducted_yoy",
    "gross_margin", "net_margin", "roe", "roe_diluted", "debt_ratio",
]
_PLAIN_NUMERIC = ["eps", "bps", "ocf_ps"]


def clean_financial(df: pd.DataFrame) -> pd.DataFrame:
    """清洗财务摘要：中文单位金额→元、百分比→数值、缺失标记→NaN，按报告期降序。"""
    if df is None or df.empty:
        return pd.DataFrame()
    data = df.copy()
    data.columns = [str(c).strip() for c in data.columns]
    # 同名列映射时保留第一个出现的（如“营业总收入/营业收入”择一）
    rename_map: Dict[str, str] = {}
    for raw, std in FINANCIAL_COLUMN_MAP.items():
        if raw in data.columns and std not in rename_map.values():
            rename_map[raw] = std
    data = data.rename(columns=rename_map)

    if "period" not in data:
        raise ValueError("财务数据缺少『报告期』列")
    data["period"] = pd.to_datetime(data["period"], errors="coerce")
    data = data.dropna(subset=["period"]).drop_duplicates(subset=["period"])

    for col in _AMOUNT_FIELDS:
        if col in data.columns:
            data[col] = data[col].map(parse_cn_number)
    for col in _PERCENT_FIELDS:
        if col in data.columns:
            data[col] = data[col].map(parse_percent)
    for col in _PLAIN_NUMERIC:
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce")

    return data.sort_values("period", ascending=False).reset_index(drop=True)


def summarize_financial(clean: pd.DataFrame, recent_n: int = 5) -> Dict[str, Any]:
    """提取最近若干期关键财务指标与同比趋势。"""
    if clean is None or clean.empty:
        return {}
    head = clean.head(recent_n)
    latest = clean.iloc[0]

    def row_get(row: pd.Series, col: str, ndigits: int = 4) -> Optional[float]:
        return _safe_round(row[col], ndigits) if col in clean.columns else None

    periods: List[Dict[str, Any]] = []
    for _, row in head.iterrows():
        periods.append(
            {
                "period": row["period"].strftime("%Y-%m-%d"),
                "revenue_yuan": row_get(row, "revenue", 0),
                "net_profit_yuan": row_get(row, "net_profit", 0),
                "revenue_yoy_pct": row_get(row, "revenue_yoy", 2),
                "net_profit_yoy_pct": row_get(row, "net_profit_yoy", 2),
                "gross_margin_pct": row_get(row, "gross_margin", 2),
                "roe_pct": row_get(row, "roe", 2),
                "eps": row_get(row, "eps", 4),
            }
        )

    summary = {
        "latest_period": latest["period"].strftime("%Y-%m-%d"),
        "latest": {
            "revenue_yuan": row_get(latest, "revenue", 0),
            "net_profit_yuan": row_get(latest, "net_profit", 0),
            "revenue_yoy_pct": row_get(latest, "revenue_yoy", 2),
            "net_profit_yoy_pct": row_get(latest, "net_profit_yoy", 2),
            "gross_margin_pct": row_get(latest, "gross_margin", 2),
            "net_margin_pct": row_get(latest, "net_margin", 2),
            "roe_pct": row_get(latest, "roe", 2),
            "eps": row_get(latest, "eps", 4),
            "bps": row_get(latest, "bps", 4),
            "debt_ratio_pct": row_get(latest, "debt_ratio", 2),
        },
        "recent_periods": periods,
    }
    return summary


def clean_info(df: pd.DataFrame) -> Dict[str, str]:
    """清洗东财个股信息（两列长表 item/value）为字典。"""
    if df is None or df.empty:
        return {}
    cols = list(df.columns)
    # 东财接口通常返回两列：item / value
    if len(cols) >= 2:
        try:
            return {str(k).strip(): str(v).strip() for k, v in zip(df[cols[0]], df[cols[1]])}
        except Exception:  # noqa: BLE001
            return {}
    return {}
