"""多公司横向对比与行业内排序。

批量抓取多家公司的核心指标，统一口径后生成对比表与排名，
支持按市盈率、营收增速、净利润、技术面强弱等维度排序。
"""
from __future__ import annotations

from dataclasses import dataclass
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


def _clip100(v: float) -> float:
    """把打分裁剪到 0-100。"""
    return round(max(0.0, min(100.0, float(v))), 1)


def _clip10(v: float) -> float:
    """把打分裁剪到 0-10。"""
    return round(max(0.0, min(10.0, float(v))), 1)


def _mean_or_none(values):
    """对可用（非 None）的打分求均值；全缺则返回 None。"""
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


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
            # 注意：键名必须与 cleaner.summarize_financial / summarize_price 的
            # 实际产出保持一致（revenue_yuan / revenue_yoy_pct / period_return_pct ...），
            # 否则整张对比表会退化成全是 "-"。
            fin = bundle.financial_summary.get("latest", {}) or {}
            price = bundle.price_summary or {}
            val = bundle.valuation or {}
            flow = bundle.fund_flow or {}
            tech = bundle.technicals or {}

            rows.append(CompanyRow(
                symbol=bundle.symbol,
                name=bundle.name,
                source=bundle.source,
                pe=_r(_g(val, "pe")),
                pb=_r(_g(val, "pb")),
                revenue=_r(_g(fin, "revenue_yuan")),
                net_profit=_r(_g(fin, "net_profit_yuan")),
                revenue_yoy=_r(_g(fin, "revenue_yoy_pct")),
                profit_yoy=_r(_g(fin, "net_profit_yoy_pct")),
                gross_margin=_r(_g(fin, "gross_margin_pct")),
                roe=_r(_g(fin, "roe_pct")),
                period_return=_r(_g(price, "period_return_pct")),
                main_flow=_r(_g(flow, "main_net_inflow")),
                trend=tech.get("trend", "") or "",
                signal_count=len(tech.get("signals", []) or []),
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
        asc = field_name in ("pe", "pb")   # 估值类：越低越便宜 -> 升序

        def key(r):
            v = getattr(r, field_name, None)
            if v is None:
                # 缺失值永远排在最后（无论升序还是降序），
                # 否则降序时抓取失败的公司会排到第 1 名。
                return (1, 0.0)
            return (0, v if asc else -v)

        return sorted(rows, key=key)

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

    return _build_radar_figure(rows, go).to_html(full_html=False, include_plotlyjs="cdn")


RADAR_DIMENSIONS = ["估值水平", "营收增长", "利润增长", "盈利能力", "技术面强弱"]


def _radar_scores(row: CompanyRow) -> List[float]:
    """把各指标归一化成 0-100 分（雷达图用）。

    缺失（含 PE <= 0 这种无意义值）用 50 分中性占位，
    不再用 `or 20` 之类伪造一个"看起来合理"的数值——那会让亏损公司被算成低估。
    """
    scores = []
    # 估值：PE 越低分越高（反向）；<=0 视为缺失
    if row.pe is not None and row.pe > 0:
        scores.append(_clip100(100 - (row.pe - 10) * 5))
    else:
        scores.append(50.0)
    # 营收增长
    scores.append(_clip100(50 + (row.revenue_yoy or 0) * 2))
    # 利润增长
    scores.append(_clip100(50 + (row.profit_yoy or 0) * 2))
    # 盈利能力（毛利率 + ROE，各占一半）
    gm_part = _clip100((row.gross_margin or 0) * 2)
    roe_part = _clip100((row.roe or 0) * 5)
    scores.append(round((gm_part + roe_part) / 2, 1))
    # 技术面（涨跌幅 + 信号数）
    ret = row.period_return or 0
    signal = row.signal_count or 0
    scores.append(_clip100(50 + ret * 5 + signal * 10))
    return scores


def _build_radar_figure(rows: List[CompanyRow], go):
    """构建多公司对比雷达图（Plotly Figure）。"""
    fig = go.Figure()
    for row in rows[:5]:  # 最多画5家，不然太挤
        fig.add_trace(go.Scatterpolar(
            r=_radar_scores(row),
            theta=RADAR_DIMENSIONS,
            fill="toself",
            name=f"{row.name}({row.symbol})",
        ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True,
        title="多公司对比雷达图（0-100分，越高越好）",
    )
    return fig


def radar_figure_json(rows: List[CompanyRow]) -> Optional[Dict[str, Any]]:
    """返回 Plotly 图的 JSON 结构，供 Web 前端用 plotly.js 渲染。"""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None
    return _build_radar_figure(rows, go).to_plotly_json()


def _valuation_percentile_rows(rows: List[CompanyRow]) -> List[Dict[str, Any]]:
    """计算每家公司 PE/PB 在同侪中的分位。

    分位口径：**便宜于同侪的比例**（0-100）。PE 越低 => 分位越高（越便宜）。
    只有 1 家公司时不构成"同侪"，分位返回 None。
    """
    pe_map = {r.symbol: r.pe for r in rows if r.pe is not None and r.pe > 0}
    pb_map = {r.symbol: r.pb for r in rows if r.pb is not None and r.pb > 0}

    def _cheap_pct(value, pool):
        if value is None or len(pool) < 2:
            return None
        cheaper = sum(1 for v in pool.values() if v > value)   # 比它贵的家数
        return round(cheaper / (len(pool) - 1) * 100, 1)       # 与其余同侪比较

    out = []
    for r in rows:
        pe = pe_map.get(r.symbol)
        pb = pb_map.get(r.symbol)
        out.append({
            "symbol": r.symbol,
            "name": r.name,
            "pe": pe,
            "pb": pb,
            "pe_cheap_pct": _cheap_pct(pe, pe_map),
            "pb_cheap_pct": _cheap_pct(pb, pb_map),
        })
    return out


def _build_valuation_figure(rows: List[CompanyRow], go):
    """构建估值分位图（真正计算分位，而不是画原始 PE/PB 柱）。"""
    data = _valuation_percentile_rows(rows)
    labeled = [d for d in data if d["pe_cheap_pct"] is not None]
    if not labeled:
        return None

    labels = [f"{d['name']}({d['symbol']})" for d in labeled]
    pe_pct = [d["pe_cheap_pct"] for d in labeled]
    pb_pct = [d["pb_cheap_pct"] if d["pb_cheap_pct"] is not None else None for d in labeled]
    pe_raw = [d["pe"] for d in labeled]
    pb_raw = [d["pb"] for d in labeled]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=pe_pct, name="PE 分位",
        marker_color="#74b9ff",
        customdata=pe_raw,
        hovertemplate="%{x}<br>PE 分位 %{y:.1f}%<br>PE=%{customdata:.2f}<extra></extra>",
    ))
    if any(v is not None for v in pb_pct):
        fig.add_trace(go.Bar(
            x=labels, y=pb_pct, name="PB 分位",
            marker_color="#fab1a0",
            customdata=pb_raw,
            hovertemplate="%{x}<br>PB 分位 %{y:.1f}%<br>PB=%{customdata:.2f}<extra></extra>",
        ))

    fig.update_layout(
        title="估值分位（便宜于同侪的比例，越高越便宜）",
        xaxis_title="公司",
        yaxis_title="分位(%)",
        yaxis=dict(range=[0, 100]),
        barmode="group",
    )
    return fig


def valuation_percentile_html(rows: List[CompanyRow]) -> str:
    """估值分位图（PE/PB 在同侪中的位置）。"""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return ""
    fig = _build_valuation_figure(rows, go)
    if fig is None:
        return ""
    # 与雷达图同时导出时，plotly.js 由其中一个注入即可，避免重复
    return fig.to_html(full_html=False, include_plotlyjs=False)


def valuation_figure_json(rows: List[CompanyRow]) -> Optional[Dict[str, Any]]:
    """返回估值分位图的 JSON 结构，供 Web 前端渲染。"""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None
    fig = _build_valuation_figure(rows, go)
    return fig.to_plotly_json() if fig is not None else None


def factor_analysis(rows: List[CompanyRow]) -> List[Dict[str, Any]]:
    """多因子分析（估值/成长/盈利/动量），得分统一为 0-10 分。

    设计原则：缺失就是缺失——不用 `or 20` 之类伪造中性值，
    某维度全部缺失时该维度得分记为 None 并从综合分权重中剔除；
    亏损公司（PE<=0）不再被算成"最便宜"。
    """
    results: List[Dict[str, Any]] = []
    for row in rows:
        factors: List[Dict[str, Any]] = []
        missing: List[str] = []

        # ---- 估值因子：PE/PB 越低越好（0-10）----
        pe_score = pb_score = None
        if row.pe is not None and row.pe > 0:
            pe_score = _clip10(10 - (row.pe - 10) / 3)      # PE=10 -> 10分, PE=40 -> 0分
        if row.pb is not None and row.pb > 0:
            pb_score = _clip10(10 - (row.pb - 1) * (10 / 9))  # PB=1 -> 10分, PB=10 -> 0分
        value_factor = _mean_or_none([pe_score, pb_score])
        if value_factor is None:
            missing.append("估值")
        factors.append({
            "name": "估值",
            "score": _clip10(value_factor) if value_factor is not None else None,
            "comment": (
                "PE/PB 缺失或为负（亏损），无法评估估值"
                if value_factor is None
                else f"PE={row.pe if row.pe is not None else '—'}，PB={row.pb if row.pb is not None else '—'}，越低越便宜"
            ),
        })

        # ---- 成长因子：营收/净利增速（0-10，0% 记 5 分）----
        rev_score = _clip10(5 + row.revenue_yoy / 5) if row.revenue_yoy is not None else None
        profit_score = _clip10(5 + row.profit_yoy / 5) if row.profit_yoy is not None else None
        growth_factor = _mean_or_none([rev_score, profit_score])
        if growth_factor is None:
            missing.append("成长")
        factors.append({
            "name": "成长",
            "score": _clip10(growth_factor) if growth_factor is not None else None,
            "comment": (
                "营收/净利增速缺失"
                if growth_factor is None
                else f"营收同比 {row.revenue_yoy if row.revenue_yoy is not None else '—'}%，"
                     f"净利同比 {row.profit_yoy if row.profit_yoy is not None else '—'}%"
            ),
        })

        # ---- 盈利因子：毛利率/ROE（0-10）----
        gm_score = _clip10(row.gross_margin / 5) if row.gross_margin is not None else None
        roe_score = _clip10(row.roe / 2) if row.roe is not None else None
        profit_factor = _mean_or_none([gm_score, roe_score])
        if profit_factor is None:
            missing.append("盈利")
        factors.append({
            "name": "盈利",
            "score": _clip10(profit_factor) if profit_factor is not None else None,
            "comment": (
                "毛利率/ROE 缺失"
                if profit_factor is None
                else f"毛利率 {row.gross_margin if row.gross_margin is not None else '—'}%，"
                     f"ROE {row.roe if row.roe is not None else '—'}%"
            ),
        })

        # ---- 动量因子：区间涨跌 + 技术信号（0-10）----
        if row.period_return is None and not row.signal_count:
            momentum_score = None
        else:
            momentum_score = _clip10(5 + (row.period_return or 0) / 5 + (row.signal_count or 0) * 0.5)
        if momentum_score is None:
            missing.append("动量")
        factors.append({
            "name": "动量",
            "score": _clip10(momentum_score) if momentum_score is not None else None,
            "comment": (
                "区间涨跌幅与技术信号缺失"
                if momentum_score is None
                else f"区间涨跌 {row.period_return if row.period_return is not None else 0}%，"
                     f"技术信号 {row.signal_count} 个"
            ),
        })

        # ---- 综合评分：权重在可用维度上重新归一 ----
        weights = {"value": 0.25, "growth": 0.30, "profit": 0.25, "momentum": 0.20}
        pairs = [
            (value_factor, weights["value"]),
            (growth_factor, weights["growth"]),
            (profit_factor, weights["profit"]),
            (momentum_score, weights["momentum"]),
        ]
        usable = [(v, w) for v, w in pairs if v is not None]
        if usable:
            wsum = sum(w for _, w in usable)
            total_score = sum(v * w for v, w in usable) / wsum
        else:
            total_score = None

        results.append({
            "symbol": row.symbol,
            "name": row.name,
            "factors": factors,
            "value_factor": _clip10(value_factor) if value_factor is not None else None,
            "growth_factor": _clip10(growth_factor) if growth_factor is not None else None,
            "profit_factor": _clip10(profit_factor) if profit_factor is not None else None,
            "momentum_factor": _clip10(momentum_score) if momentum_score is not None else None,
            "total_score": _clip10(total_score) if total_score is not None else None,
            "missing": missing,
        })

    # 按综合评分排序；无评分的排最后
    results.sort(key=lambda x: (x["total_score"] is None, -(x["total_score"] or 0)))
    return results
