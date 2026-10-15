"""数据服务：编排 AKShare 实时抓取、清洗与离线样例回退，产出统一数据包。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from ..analysis.indicators import analyze_technicals
from ..common.logger import get_logger
from . import cache, sample_data
from .cleaner import (
    clean_financial,
    clean_info,
    clean_price,
    summarize_financial,
    summarize_price,
)
from .sources import AKShareSource, DataSourceError

logger = get_logger("data.service")


def _round(value, ndigits: int = 2):
    """安全取整：None/NaN → None。"""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return round(v, ndigits)


@dataclass
class CompanyBundle:
    """单家公司的清洗后数据包（全部为可 JSON 序列化的原生结构）。"""

    symbol: str
    name: str
    source: str = "sample"                  # live / sample
    as_of: str = ""
    info: Dict[str, str] = field(default_factory=dict)
    news: List[Dict[str, str]] = field(default_factory=list)
    price_summary: Dict[str, Any] = field(default_factory=dict)
    financial_summary: Dict[str, Any] = field(default_factory=dict)
    valuation: Dict[str, Any] = field(default_factory=dict)
    fund_flow: Dict[str, Any] = field(default_factory=dict)
    technicals: Dict[str, Any] = field(default_factory=dict)
    price_clean: Optional[pd.DataFrame] = None
    financial_clean: Optional[pd.DataFrame] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "source": self.source,
            "as_of": self.as_of,
            "info": self.info,
            "news": self.news,
            "price_summary": self.price_summary,
            "financial_summary": self.financial_summary,
            "valuation": self.valuation,
            "fund_flow": self.fund_flow,
            "technicals": self.technicals,
        }


class DataService:
    """对外统一的数据入口，实时抓取失败时安全回退到离线样例。"""

    def __init__(self, source: Optional[AKShareSource] = None):
        self.source = source or AKShareSource()

    # ------------------------------------------------------------------
    def get_company_bundle(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        prefer_live: bool = True,
        use_cache: bool = True,
    ) -> CompanyBundle:
        """获取公司行情 + 财务 + 资讯数据包。

        :param prefer_live: 优先实时抓取；失败自动回退样例。为 False 时直接用样例。
        :param use_cache: 是否启用当天缓存（同一天同一只股票直接复用）
        """
        code = self.source.normalize_symbol(symbol)
        end_date = end_date or datetime.now().strftime("%Y%m%d")
        start_date = start_date or (
            datetime.now() - timedelta(days=365)
        ).strftime("%Y%m%d")

        # 先查当天缓存
        if prefer_live and use_cache:
            cached = cache.get_cached_bundle(code)
            if cached:
                return CompanyBundle(
                    symbol=cached["symbol"],
                    name=cached["name"],
                    source=cached["source"],
                    as_of=cached["as_of"],
                    info=cached["info"],
                    news=cached["news"],
                    price_summary=cached["price_summary"],
                    financial_summary=cached["financial_summary"],
                    valuation=cached["valuation"],
                    fund_flow=cached["fund_flow"],
                    technicals=cached["technicals"],
                )

        if prefer_live:
            try:
                bundle = self._live_bundle(code, start_date, end_date)
                # 写入缓存（只存 summary 级别，不存 DataFrame）
                if use_cache:
                    cache.save_cached_bundle(code, bundle.to_dict())
                return bundle
            except DataSourceError as exc:
                logger.warning("实时数据抓取失败，回退到离线样例：%s", exc)
        return self._sample_bundle(code)

    # ------------------------------------------------------------------
    def _live_bundle(self, code: str, start_date: str, end_date: str) -> CompanyBundle:
        logger.info("实时抓取 %s 行情/财务（%s~%s）", code, start_date, end_date)
        price_raw = self.source.price_daily(code, start_date, end_date)
        fin_raw = self.source.financial_abstract(code)

        price_clean = clean_price(price_raw)
        fin_clean = clean_financial(fin_raw)
        if price_clean.empty or fin_clean.empty:
            raise DataSourceError("行情或财务清洗后为空")

        info = self._safe_info(code)
        news = self._safe_news(code)
        valuation = self._safe_valuation(code)
        fund_flow = self._safe_fund_flow(code)
        technicals = analyze_technicals(price_clean)
        name = info.get("股票简称", code)

        bundle = CompanyBundle(
            symbol=code,
            name=name,
            source="live",
            as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            info=info,
            news=news,
            price_summary=summarize_price(price_clean),
            financial_summary=summarize_financial(fin_clean),
            valuation=valuation,
            fund_flow=fund_flow,
            technicals=technicals,
            price_clean=price_clean,
            financial_clean=fin_clean,
        )
        logger.info(
            "%s 实时数据就绪：%d 个交易日、%d 期财务、%d 条资讯",
            name, len(price_clean), len(fin_clean), len(news),
        )
        return bundle

    def _sample_bundle(self, code: str) -> CompanyBundle:
        missing = sample_data.ensure_samples_exist()
        if missing:
            raise DataSourceError(
                f"离线样例不存在，请先运行 python {missing}"
            )
        price_clean = clean_price(sample_data.sample_price_raw())
        fin_clean = clean_financial(sample_data.sample_financial_raw())
        news_df = sample_data.sample_news_raw()
        news = [
            {
                "title": str(row.get("新闻标题", "")),
                "content": str(row.get("新闻内容", "")),
                "time": str(row.get("发布时间", "")),
                "media": str(row.get("文章来源", "")),
                "url": str(row.get("新闻链接", "")),
            }
            for _, row in news_df.iterrows()
        ]
        bundle = CompanyBundle(
            symbol=code,
            name=sample_data.SAMPLE_NAME,
            source="sample",
            as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            info={"数据来源": "离线虚构样例", "说明": "非真实证券，仅用于演示"},
            news=news,
            price_summary=summarize_price(price_clean),
            financial_summary=summarize_financial(fin_clean),
            price_clean=price_clean,
            financial_clean=fin_clean,
        )
        logger.info("已使用离线样例数据包（%d 交易日 / %d 期财务）", len(price_clean), len(fin_clean))
        return bundle

    # ------------------------------------------------------------------
    def _safe_info(self, code: str) -> Dict[str, str]:
        try:
            return clean_info(self.source.stock_info(code))
        except DataSourceError as exc:
            logger.info("个股信息获取失败（不影响主流程）：%s", exc)
            return {}

    def _safe_news(self, code: str) -> List[Dict[str, str]]:
        try:
            df = self.source.company_news(code)
        except DataSourceError as exc:
            logger.info("公司资讯获取失败（不影响主流程）：%s", exc)
            return []
        items: List[Dict[str, str]] = []
        for _, row in df.iterrows():
            items.append(
                {
                    "title": str(row.get("新闻标题", "")),
                    "content": str(row.get("新闻内容", "")),
                    "time": str(row.get("发布时间", "")),
                    "media": str(row.get("文章来源", "")),
                    "url": str(row.get("新闻链接", "")),
                }
            )
        return items

    def _safe_valuation(self, code: str) -> Dict[str, Any]:
        """PE/PB 估值（best-effort，失败不阻塞）。"""
        try:
            df = self.source.valuation_indicator(code)
            if df is None or df.empty:
                return {}
            latest = df.iloc[-1]
            return {
                "pe": _round(latest.get("pe")),
                "pb": _round(latest.get("pb")),
                "ps": _round(latest.get("ps")),
                "dv_ratio": _round(latest.get("dv_ratio")),
                "trade_date": str(latest.get("trade_date", "")),
            }
        except DataSourceError as exc:
            logger.info("估值指标获取失败（不影响主流程）：%s", exc)
            return {}
        except Exception as exc:  # noqa: BLE001
            logger.info("估值指标解析异常：%s", exc)
            return {}

    def _safe_fund_flow(self, code: str) -> Dict[str, Any]:
        """主力资金流向（best-effort，失败不阻塞）。"""
        try:
            df = self.source.capital_flow(code)
            if df is None or df.empty:
                return {}
            latest = df.iloc[-1]
            return {
                "date": str(latest.get("日期", "")),
                "main_net_inflow": _round(latest.get("主力净流入-净额")),
                "main_net_pct": _round(latest.get("主力净流入-净占比")),
                "small_net_inflow": _round(latest.get("小单净流入-净额")),
                "super_large_net": _round(latest.get("超大单净流入-净额")),
            }
        except DataSourceError as exc:
            logger.info("资金流向获取失败（不影响主流程）：%s", exc)
            return {}
        except Exception as exc:  # noqa: BLE001
            logger.info("资金流向解析异常：%s", exc)
            return {}

    # ------------------------------------------------------------------
    def get_industry_constituents(self, industry_name: str) -> pd.DataFrame:
        """行业成分股（best-effort，失败抛出由上层处理）。"""
        return self.source.industry_constituents(industry_name)
