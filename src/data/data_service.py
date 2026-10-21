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
from .manager import DataSourceManager, build_manager_from_settings
from .sources import AKShareSource, DataSourceError

logger = get_logger("data.service")

# 进程内只清理一次过期缓存（跨天失效目录无限增长）
_cache_cleaned = False


def _clean_expired_cache_once() -> None:
    global _cache_cleaned
    if _cache_cleaned:
        return
    _cache_cleaned = True
    try:
        removed = cache.clear_expired_cache()
        if removed:
            logger.info("已清理 %d 个过期缓存目录", removed)
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响取数
        logger.debug("清理过期缓存失败：%s", exc)


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
    # 多源交叉验证警告（未启用/无多源时为空列表）
    cross_check: List[str] = field(default_factory=list)

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
            "cross_check": self.cross_check,
        }


class DataService:
    """对外统一的数据入口，实时抓取失败时安全回退到离线样例。"""

    def __init__(
        self,
        source: Optional[AKShareSource] = None,
        manager: Optional[DataSourceManager] = None,
        settings: Optional[Any] = None,
    ):
        self.source = source or AKShareSource()
        self.settings = settings
        if settings is None:
            # 复用全局配置单例（Web/MCP/CLI 常驻进程只加载一次），
            # 以便通过 settings.data_sources 或环境变量 DATA_SOURCES 真正接入多数据源
            try:
                from ..common.config import get_settings

                settings = get_settings()
                self.settings = settings
            except Exception as exc:  # noqa: BLE001 - 配置不可用时退回单数据源
                logger.debug("加载全局配置失败，跳过：%s", exc)
        self.manager = manager if manager is not None else build_manager_from_settings(settings)

    # ------------------------------------------------------------------
    def get_company_bundle(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        prefer_live: bool = True,
        use_cache: bool = True,
        adjust: str = "qfq",
    ) -> CompanyBundle:
        """获取公司行情 + 财务 + 资讯数据包。

        :param prefer_live: 优先实时抓取；失败自动回退样例。为 False 时直接用样例。
        :param use_cache: 是否启用当天缓存（同一天、同一区间/复权直接复用）
        :param adjust: 复权方式（qfq/hfq/""），参与缓存键，避免不同复权数据串味
        """
        code = self.source.normalize_symbol(symbol)
        end_date = end_date or datetime.now().strftime("%Y%m%d")
        start_date = start_date or (
            datetime.now() - timedelta(days=365)
        ).strftime("%Y%m%d")

        if use_cache:
            _clean_expired_cache_once()

        # 先查当天缓存（键含 start/end/adjust）
        if prefer_live and use_cache:
            cached = cache.get_cached_bundle(code, start_date, end_date, adjust)
            if cached is not None:
                bundle = self._bundle_from_cache(cached)
                if bundle is not None:
                    return bundle
                logger.warning("缓存结构不可用，按未命中处理：%s", code)

        if prefer_live:
            try:
                bundle = self._live_bundle(code, start_date, end_date, adjust)
                # 写入缓存（含清洗后的 DataFrame，命中后仍可绘图）
                if use_cache:
                    cache.save_cached_bundle(
                        code,
                        bundle.to_dict(),
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust,
                        price_clean=bundle.price_clean,
                        financial_clean=bundle.financial_clean,
                    )
                return bundle
            except Exception as exc:  # noqa: BLE001
                # cleaner 抛 ValueError/KeyError（脏数据）也要走样例回退，而不是直接崩
                logger.warning("实时数据抓取失败，回退到离线样例：%s", exc)
        return self._sample_bundle(code)

    # ------------------------------------------------------------------
    @staticmethod
    def _bundle_from_cache(cached: Dict[str, Any]) -> Optional[CompanyBundle]:
        """把缓存字典安全地重建为 CompanyBundle（缺字段用默认值，坏结构返回 None）。"""
        try:
            price_clean = cached.get("price_clean")
            financial_clean = cached.get("financial_clean")
            if not isinstance(price_clean, pd.DataFrame) or price_clean.empty:
                return None
            if not isinstance(financial_clean, pd.DataFrame) or financial_clean.empty:
                return None
            return CompanyBundle(
                symbol=str(cached.get("symbol") or ""),
                name=str(cached.get("name") or ""),
                source=str(cached.get("source") or "live"),
                as_of=str(cached.get("as_of") or ""),
                info=cached.get("info") or {},
                news=cached.get("news") or [],
                price_summary=cached.get("price_summary") or {},
                financial_summary=cached.get("financial_summary") or {},
                valuation=cached.get("valuation") or {},
                fund_flow=cached.get("fund_flow") or {},
                technicals=cached.get("technicals") or {},
                price_clean=price_clean,
                financial_clean=financial_clean,
                cross_check=list(cached.get("cross_check") or []),
            )
        except Exception as exc:  # noqa: BLE001 - 坏缓存绝不能打断主流程
            logger.warning("缓存重建数据包失败：%s", exc)
            return None

    # ------------------------------------------------------------------
    def _live_bundle(self, code: str, start_date: str, end_date: str, adjust: str = "qfq") -> CompanyBundle:
        logger.info("实时抓取 %s 行情/财务（%s~%s，复权=%s）", code, start_date, end_date, adjust or "none")

        price_source = fin_source = getattr(self.source, "name", "akshare")
        # 如果有多数据源管理器，用它取数（带容灾 + 交叉验证）
        if self.manager is not None:
            price_raw, price_source = self.manager.fetch_with_failover(
                "price_daily",
                symbol=code,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            fin_raw, fin_source = self.manager.fetch_with_failover(
                "financial_abstract", symbol=code
            )
            logger.info("行情数据源：%s，财务数据源：%s", price_source, fin_source)
        else:
            price_raw = self.source.price_daily(code, start_date, end_date, adjust)
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
        # 东财口径是「股票简称」，新浪快照口径是「名称」，两边都要认，
        # 否则降级到新浪时报告标题会变成一串股票代码
        name = info.get("股票简称") or info.get("名称") or info.get("简称") or code

        # 备数据源若回退到离线样例，如实标注为 sample，避免把样例当实时数据
        offline_used = "offline" in (price_source, fin_source)
        cross_check = [] if offline_used else self._cross_check_financials(code, fin_clean)

        bundle = CompanyBundle(
            symbol=code,
            name=name,
            source="sample" if offline_used else "live",
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
            cross_check=cross_check,
        )
        if cross_check:
            logger.warning("多源交叉验证发现 %d 处数据不一致", len(cross_check))
        logger.info(
            "%s 实时数据就绪：%d 个交易日、%d 期财务、%d 条资讯",
            name, len(price_clean), len(fin_clean), len(news),
        )
        return bundle

    # ------------------------------------------------------------------
    @staticmethod
    def _summary_view(fin_clean: pd.DataFrame) -> Dict[str, Any]:
        """把清洗后财务转成交叉验证用的扁平指标字典（数据层口径）。"""
        summary = summarize_financial(fin_clean)
        latest = summary.get("latest") or {}
        view: Dict[str, Any] = dict(latest)
        view["latest_period"] = summary.get("latest_period")
        return view

    def _cross_check_financials(self, code: str, primary_fin_clean: pd.DataFrame) -> List[str]:
        """用备数据源抓取财务摘要，与主数据源做多源交叉验证。"""
        if self.manager is None or not self.manager.enable_cross_check:
            return []
        if len(self.manager.sources) < 2:
            return []

        views: List[Dict[str, Any]] = []
        primary_view = self._summary_view(primary_fin_clean)
        if primary_view:
            views.append(primary_view)

        for source in self.manager.sources[1:]:
            source_name = getattr(source, "name", source.__class__.__name__)
            try:
                raw = source.financial_abstract(code)
                fin = clean_financial(raw)
                if fin.empty:
                    continue
                views.append(self._summary_view(fin))
            except Exception as exc:  # noqa: BLE001 - 备源失败不影响主流程
                logger.info("备数据源 %s 交叉验证取数失败：%s", source_name, exc)

        if len(views) < 2:
            return []
        return self.manager.cross_check_numbers(*views)

    # ------------------------------------------------------------------
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
        except Exception as exc:  # noqa: BLE001 - 个股信息 best-effort
            logger.info("个股信息获取失败（不影响主流程）：%s", exc)
            return {}

    def _safe_news(self, code: str) -> List[Dict[str, str]]:
        try:
            df = self.source.company_news(code)
        except Exception as exc:  # noqa: BLE001 - 资讯 best-effort
            logger.info("公司资讯获取失败（不影响主流程）：%s", exc)
            return []
        items: List[Dict[str, str]] = []
        try:
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
        except Exception as exc:  # noqa: BLE001
            logger.info("公司资讯解析失败（不影响主流程）：%s", exc)
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
        except Exception as exc:  # noqa: BLE001
            logger.info("估值指标获取失败（不影响主流程）：%s", exc)
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
        except Exception as exc:  # noqa: BLE001
            logger.info("资金流向获取失败（不影响主流程）：%s", exc)
            return {}

    # ------------------------------------------------------------------
    def get_industry_constituents(self, industry_name: str) -> pd.DataFrame:
        """行业成分股（best-effort，失败抛出由上层处理）。"""
        return self.source.industry_constituents(industry_name)
