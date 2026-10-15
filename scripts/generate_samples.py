#!/usr/bin/env python3
"""生成离线演示用的“原始格式”样例数据（模拟 AKShare 返回的中文列 + 亿/万/%/False）。

运行：
    python scripts/generate_samples.py

产物：data/samples/{sample_price_raw.csv,sample_financial_raw.csv,sample_news_raw.csv}
这些样例为**虚构演示数据，不对应任何真实证券**，用于在无网络/未安装 akshare 时
跑通“抓取→清洗→分析→交叉校验”全流程。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "samples"
SAMPLE_SYMBOL = "000000"


def gen_price() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2025-01-02", periods=120)
    n = len(dates)
    drift, vol = 0.0006, 0.018
    ret = rng.normal(drift, vol, n)
    close = 28.0 * np.cumprod(1 + ret)

    rows = []
    prev_close = close[0]
    for i, d in enumerate(dates):
        c = round(float(close[i]), 2)
        o = round(prev_close * (1 + rng.normal(0, 0.004)), 2)
        h = round(max(o, c) * (1 + abs(rng.normal(0, 0.006))), 2)
        low = round(min(o, c) * (1 - abs(rng.normal(0, 0.006))), 2)
        volume = int(rng.integers(80000, 400000))
        amount = round(c * volume * 100, 0)
        pct = round((c / prev_close - 1) * 100, 2) if i else 0.0
        change = round(c - prev_close, 2) if i else 0.0
        amplitude = round((h - low) / prev_close * 100, 2)
        turnover = round(float(rng.uniform(0.3, 2.5)), 2)
        rows.append(
            {
                "日期": d.strftime("%Y-%m-%d"),
                "股票代码": SAMPLE_SYMBOL,
                "开盘": o, "收盘": c, "最高": h, "最低": low,
                "成交量": volume, "成交额": amount,
                "振幅": amplitude, "涨跌幅": pct, "涨跌额": change, "换手率": turnover,
            }
        )
        prev_close = c
    return pd.DataFrame(rows)


def gen_financial() -> pd.DataFrame:
    # 故意混入中文单位、百分号、以及 False 缺失，贴近同花顺真实返回
    data = [
        ["2025-12-31", "31.80亿", "9.40%", "30.90亿", "9.10%", "152.30亿", "12.50%",
         "1.27", "8.14", "1.05", "1.36", "42.30%", "20.88%", "15.60%"],
        ["2025-09-30", "22.10亿", "8.10%", "21.40亿", "7.90%", "108.60亿", "11.20%",
         "0.88", "7.75", "1.02", "0.94", "41.80%", "20.35%", "11.30%"],
        ["2025-06-30", "14.30亿", "7.50%", "13.90亿", "7.20%", "70.20亿", "10.80%",
         "0.57", "7.44", "0.99", "0.61", "41.50%", "20.37%", "7.40%"],
        ["2025-03-31", "6.90亿", "6.80%", "6.70亿", "6.50%", "33.50亿", "9.90%",
         "0.28", "7.15", "0.97", "0.30", "41.20%", "20.60%", "3.60%"],
        ["2024-12-31", "29.07亿", "5.20%", "False", "False", "135.38亿", "8.30%",
         "1.16", "7.62", "0.92", "1.21", "41.90%", "21.47%", "14.60%"],
    ]
    cols = [
        "报告期", "净利润", "净利润同比增长率", "扣非净利润", "扣非净利润同比增长率",
        "营业总收入", "营业总收入同比增长率", "基本每股收益", "每股净资产",
        "每股资本公积金", "每股经营现金流", "销售毛利率", "销售净利率", "净资产收益率",
    ]
    return pd.DataFrame(data, columns=cols)


def gen_news() -> pd.DataFrame:
    rows = [
        [
            SAMPLE_SYMBOL,
            "演示样本公司(000000)：2025年净利润31.80亿元，同比增长9.40%",
            "2026年3月，演示样本公司发布2025年年报。全年营业总收入152.30亿元，"
            "同比增长12.50%；净利润31.80亿元，同比增长9.40%；基本每股收益1.27元。"
            "（本内容为虚构教学样例）",
            "2026-03-18 09:30:00", "演示财经", "https://example.com/sample/annual",
        ],
        [
            SAMPLE_SYMBOL,
            "演示样本公司拟回购股份用于股权激励",
            "公司公告拟以自有资金回购股份，回购金额不低于2亿元、不超过4亿元，"
            "用于员工持股计划。（本内容为虚构教学样例）",
            "2026-02-10 18:00:00", "演示公告", "https://example.com/sample/buyback",
        ],
        [
            SAMPLE_SYMBOL,
            "机构调研：演示样本公司毛利率维持在42%以上",
            "近期多家机构调研演示样本公司，公司表示主营业务毛利率稳定在42%左右，"
            "新产品线有望在2026年贡献增量。（本内容为虚构教学样例）",
            "2026-01-22 12:00:00", "演示调研", "https://example.com/sample/research",
        ],
    ]
    cols = ["关键词", "新闻标题", "新闻内容", "发布时间", "文章来源", "新闻链接"]
    return pd.DataFrame(rows, columns=cols)


def main() -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "sample_price_raw.csv": gen_price(),
        "sample_financial_raw.csv": gen_financial(),
        "sample_news_raw.csv": gen_news(),
    }
    for name, df in outputs.items():
        path = SAMPLE_DIR / name
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"written {path.relative_to(ROOT)}  shape={df.shape}")


if __name__ == "__main__":
    main()
