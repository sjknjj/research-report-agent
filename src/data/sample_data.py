"""离线样例数据加载。

在未安装 akshare、无网络或显式要求离线时，用 data/samples 下的虚构样例
（AKShare 原始“脏格式”）替代实时抓取，保证全流程可运行、可测试、可演示。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "samples"
SAMPLE_SYMBOL = "000000"
SAMPLE_NAME = "演示样本公司(000000)（虚构样例，非真实证券）"

SAMPLE_ANNOUNCEMENT = """证券代码：000000  证券简称：演示样本公司  公告编号：2026-009
演示样本公司 2025 年年度业绩快报（教学演示样例，数据均为虚构）

本公司董事会及全体董事保证本公告内容不存在任何虚假记载、误导性陈述或者重大遗漏。

一、2025 年度主要财务数据
报告期内，公司实现营业总收入 152.30 亿元，较上年同期增长 12.50%；
归属于上市公司股东的净利润 31.80 亿元，较上年同期增长 9.40%；
扣除非经常性损益后的净利润 30.90 亿元，同比增长 9.10%；
基本每股收益 1.27 元；销售毛利率 42.30%；加权平均净资产收益率 15.60%。

二、经营情况说明
报告期内，公司主营业务保持稳健增长，毛利率维持在 42% 以上；
新产品线逐步放量，费用率稳中有降，整体盈利质量良好。

三、风险提示
本快报数据为初步核算数据，最终以公司披露的 2025 年年度报告为准，
敬请广大投资者注意投资风险。
"""


@lru_cache(maxsize=1)
def sample_price_raw() -> pd.DataFrame:
    """以字符串 dtype 读取原始行情 CSV，保留 AKShare 原始格式。"""
    return pd.read_csv(SAMPLE_DIR / "sample_price_raw.csv", dtype=str)


@lru_cache(maxsize=1)
def sample_financial_raw() -> pd.DataFrame:
    return pd.read_csv(SAMPLE_DIR / "sample_financial_raw.csv", dtype=str)


@lru_cache(maxsize=1)
def sample_news_raw() -> pd.DataFrame:
    return pd.read_csv(SAMPLE_DIR / "sample_news_raw.csv", dtype=str)


def sample_announcement_text() -> str:
    return SAMPLE_ANNOUNCEMENT


def ensure_samples_exist() -> Optional[Path]:
    """样例 CSV 缺失时返回生成脚本路径（提示调用方先生成）。"""
    if not (SAMPLE_DIR / "sample_price_raw.csv").exists():
        return Path(__file__).resolve().parents[2] / "scripts" / "generate_samples.py"
    return None
