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


def radar_chart_html(rows: List[CompanyRow]) -> str:
    """多公司对比雷达图（Plotly 交互式）。

    维度：估值（PE/PB反向）、成长（营收/净利增速）、盈利（毛利率/ROE）、技术面。
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return ""

    # 维度定义
    dimensions = ["估值水平", "营收增长", "利润增长", "盈利能力", "技术面强弱"]

    def _score(row: CompanyRow) -> List[float]:
        """把各指标归一化成 0-100 分。"""
        scores = []
        # 估值：PE/PB 越低分越高（反向）
        pe = row.pe or 20
        pe_score = max(0, min(100, 100 - (pe - 10) * 5))
        scores.append(pe_score)
        # 营收增长
        rev = row.revenue_yoy or 0
        rev_score = max(0, min(100, 50 + rev * 2))
        scores.append(rev_score)
        # 利润增长
        profit = row.profit_yoy or 0
        profit_score = max(0, min(100, 50 + profit * 2))
        scores.append(profit_score)
        # 盈利能力（毛利率+ROE平均）
        gm = row.gross_margin or 30
        roe = row.roe or 10
        profit_ability = (gm + roe * 2) / 3
        scores.append(max(0, min(100, profit_ability * 1.5)))
        # 技术面（涨跌幅 + 信号数）
        ret = row.period_return or 0
        signal = row.signal_count or 0
        tech_score = max(0, min(100, 50 + ret * 5 + signal * 10))
        scores.append(tech_score)
        return scores

    fig = go.Figure()
    for row in rows[:5]:  # 最多画5家，不然太挤
        scores = _score(row)
        fig.add_trace(go.Scatterpolar(
            r=scores,
            theta=dimensions,
            fill="toself",
            name=f"{row.name}({row.symbol})",
        ))

    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True,
        title="多公司对比雷达图（0-100分，越高越好）",
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def valuation_percentile_html(rows: List[CompanyRow]) -> str:
    """估值分位图（PE/PB 在同行中的位置）。"""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return ""

    valid_pe = [r.pe for r in rows if r.pe and r.pe > 0]
    valid_pb = [r.pb for r in rows if r.pb and r.pb > 0]

    if not valid_pe:
        return ""

    fig = go.Figure()

    # PE 柱状图
    fig.add_trace(go.Bar(
        x=[f"{r.name}({r.symbol})" for r in rows if r.pe and r.pe > 0],
        y=[r.pe for r in rows if r.pe and r.pe > 0],
        name="市盈率(PE)",
        marker_color="#74b9ff",
    ))

    # PB 柱状图
    if valid_pb:
        fig.add_trace(go.Bar(
            x=[f"{r.name}({r.symbol})" for r in rows if r.pb and r.pb > 0],
            y=[r.pb for r in rows if r.pb and r.pb > 0],
            name="市净率(PB)",
            marker_color="#fab1a0",
        ))

    fig.update_layout(
        title="估值对比（越低越便宜）",
        xaxis_title="公司",
        yaxis_title="数值",
        barmode="group",
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def factor_analysis(rows: List[CompanyRow]) -> List[Dict[str, Any]]:
    """多因子分析（估值/成长/盈利/动量）。

    返回每家公司的因子得分和综合评分。
    """
    results = []
    for row in rows:
        # 估值因子（PE/PB 越低越好，反向）
        pe = row.pe or 20
        pe_score = max(0, min(100, 100 - (pe - 10) * 5))

        pb = row.pb or 2
        pb_score = max(0, min(100, 100 - (pb - 1) * 20))

        value_factor = (pe_score + pb_score) / 2

        # 成长因子（营收/净利增速越高越好）
        rev = row.revenue_yoy or 0
        rev_score = max(0, min(100, 50 + rev * 2))

        profit = row.profit_yoy or 0
        profit_score = max(0, min(100, 50 + profit * 2))

        growth_factor = (rev_score + profit_score) / 2

        # 盈利因子（毛利率/ROE 越高越好）
        gm = row.gross_margin or 30
        gm_score = max(0, min(100, gm * 1.5))

        roe = row.roe or 10
        roe_score = max(0, min(100, roe * 5))

        profit_factor = (gm_score + roe_score) / 2

        # 动量因子（近期涨跌幅越高越好）
        ret = row.period_return or 0
        momentum_score = max(0, min(100, 50 + ret * 5))

        # 综合评分（加权平均）
        total_score = (
            value_factor * 0.25 +      # 估值权重 25%
            growth_factor * 0.30 +      # 成长权重 30%
            profit_factor * 0.25 +      # 盈利权重 25%
            momentum_score * 0.20       # 动量权重 20%
        )

        results.append({
            "symbol": row.symbol,
            "name": row.name,
            "value_factor": round(value_factor, 1),
            "growth_factor": round(growth_factor, 1),
            "profit_factor": round(profit_factor, 1),
            "momentum_factor": round(momentum_score, 1),
            "total_score": round(total_score, 1),
        })

    # 按综合评分排序
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results
