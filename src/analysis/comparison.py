"""多公司横向对比与行业内排序。

批量抓取多家公司的核心指标，统一口径后生成对比表与排名，
支持按市盈率、营收增速、净利润、技术面强弱等维度排序。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..data.data_service import DataService
from ..data.sources import DataSourceError


@dataclass
class CompanyRow:
    symbol: str
    name: str
    source: str = "sample"
    pe: Optional[float] = None
    pb: Optional[float] = None
    revenue: Optional[float] = None
    net_profit: Optional[float] = None
    revenue_yoy: Optional[float] = None
    profit_yoy: Optional[float] = None
    gross_margin: Optional[float] = None
    roe: Optional[float] = None
    period_return: Optional[float] = None
    main_flow: Optional[float] = None
    trend: str = ""
    signal_count: int = 0


def _g(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _r(v, n=2):
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return round(f, n)
    except (TypeError, ValueError):
        return None


class ComparisonService:
    def __init__(self, data_service: Optional[DataService] = None):
        self.data = data_service or DataService()

    def compare(self, symbols: List[str], prefer_live: bool = True) -> List[CompanyRow]:
        rows: List[CompanyRow] = []
        for sym in symbols:
            try:
                bundle = self.data.get_company_bundle(sym, prefer_live=prefer_live)
            except DataSourceError:
                rows.append(CompanyRow(symbol=sym, name=f"{sym}(抓取失败)"))
                continue
            fin = bundle.financial_summary.get("latest", {})
            price = bundle.price_summary
            val = bundle.valuation
            flow = bundle.fund_flow
            tech = bundle.technicals

            rows.append(CompanyRow(
                symbol=bundle.symbol,
                name=bundle.name,
                source=bundle.source,
                pe=_r(_g(val, "pe")),
                pb=_r(_g(val, "pb")),
                revenue=_r(_g(fin, "revenue", "营业总收入")),
                net_profit=_r(_g(fin, "net_profit", "归母净利润")),
                revenue_yoy=_r(_g(fin, "revenue_yoy", "营收同比")),
                profit_yoy=_r(_g(fin, "net_profit_yoy", "净利润同比", "归母净利润同比")),
                gross_margin=_r(_g(fin, "gross_margin", "销售毛利率")),
                roe=_r(_g(fin, "roe", "净资产收益率")),
                period_return=_r(_g(price, "period_return", "区间涨跌幅")),
                main_flow=_r(_g(flow, "main_net_inflow")),
                trend=tech.get("trend", ""),
                signal_count=len(tech.get("signals", [])),
            ))
        return rows

    def rank_table(self, rows: List[CompanyRow], by: str = "profit_yoy") -> List[CompanyRow]:
        """按指定指标排序；估值类指标（pe/pb）升序（越低越便宜），其余降序。"""
        key_map = {
            "profit_yoy": "profit_yoy", "revenue_yoy": "revenue_yoy",
            "net_profit": "net_profit", "revenue": "revenue",
            "roe": "roe", "period_return": "period_return",
            "pe": "pe", "pb": "pb",
        }
        field_name = key_map.get(by, by)
        asc = field_name in ("pe", "pb")

        def key(r):
            v = getattr(r, field_name, None)
            return (v is None, v if v is not None else 0)

        return sorted(rows, key=key, reverse=not asc)

    def to_dict(self, rows: List[CompanyRow]) -> List[Dict[str, Any]]:
        return [vars(r) for r in rows]

    def render_markdown(self, rows: List[CompanyRow], by: str = "profit_yoy") -> str:
        ranked = self.rank_table(rows, by)
        label_map = {
            "profit_yoy": "净利润同比(%)", "revenue_yoy": "营收同比(%)",
            "net_profit": "净利润(亿)", "pe": "市盈率",
        }
        lines = [
            f"# 多公司横向对比（按 {label_map.get(by, by)} 排序）", "",
            "| 排名 | 公司 | 代码 | PE | PB | 营收同比% | 净利同比% | 毛利率% | ROE% | 区间涨跌% | 技术面 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for i, r in enumerate(ranked, 1):
            def fmt(v, n=1):
                return "-" if v is None else f"{v:.{n}f}"
            lines.append(
                f"| {i} | {r.name} | {r.symbol} | {fmt(r.pe)} | {fmt(r.pb)} | "
                f"{fmt(r.revenue_yoy)} | {fmt(r.profit_yoy)} | {fmt(r.gross_margin)} | "
                f"{fmt(r.roe)} | {fmt(r.period_return)} | {r.trend or '-'} |"
            )
        lines.append("")
        lines.append("> 估值指标 PE/PB 越低越便宜；增速/ROE/区间涨跌越高越优；数据口径见各公司事实包。")
        return "\n".join(lines)
