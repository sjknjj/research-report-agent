"""事实库：把清洗后的结构化数据转成“可核验事实”，并渲染为给大模型的事实包。

设计意图（反幻觉的第一道防线——输入侧约束）：
- 大模型只被允许使用 :meth:`FactBase.fact_pack_text` 中列出的数字；
- 每条事实都带标准单位与报告期/口径，便于输出侧逐条核对；
- 金额统一以“元”存储、展示时换算为“亿元”，比率统一为百分数数值。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..data.data_service import CompanyBundle

_YI = 1e8
_WAN = 1e4


def fmt_amount(yuan: Optional[float]) -> str:
    """元 -> 亿元字符串。"""
    if yuan is None:
        return "—"
    return f"{yuan / _YI:.2f} 亿元"


def fmt_pct(value: Optional[float], signed: bool = True) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.2f}%"


@dataclass
class Fact:
    key: str            # 标准指标键
    label: str          # 中文名
    value: Optional[float]
    unit: str           # yuan / percent / price
    period: str = ""    # 报告期或区间口径
    source: str = ""

    def display(self) -> str:
        if self.value is None:
            return f"{self.label}：暂无数据"
        if self.unit == "yuan":
            return f"{self.label}：{fmt_amount(self.value)}"
        if self.unit == "percent":
            return f"{self.label}：{fmt_pct(self.value, signed=False)}"
        return f"{self.label}：{self.value:.2f}"


class FactBase:
    """从 :class:`CompanyBundle` 构建的可核验事实集合。"""

    def __init__(self, bundle: CompanyBundle):
        self.bundle = bundle
        self.facts: Dict[str, Fact] = {}
        self._build()

    def get(self, key: str) -> Optional[Fact]:
        return self.facts.get(key)

    def _build(self) -> None:
        fin = self.bundle.financial_summary.get("latest", {})
        period = self.bundle.financial_summary.get("latest_period", "")
        price = self.bundle.price_summary

        def add(key, label, value, unit, per=""):
            self.facts[key] = Fact(key, label, value, unit, per or period, self.bundle.source)

        add("revenue", "营业总收入", fin.get("revenue_yuan"), "yuan")
        add("net_profit", "归母净利润", fin.get("net_profit_yuan"), "yuan")
        add("revenue_yoy", "营业总收入同比增速", fin.get("revenue_yoy_pct"), "percent")
        add("net_profit_yoy", "归母净利润同比增速", fin.get("net_profit_yoy_pct"), "percent")
        add("gross_margin", "销售毛利率", fin.get("gross_margin_pct"), "percent")
        add("net_margin", "销售净利率", fin.get("net_margin_pct"), "percent")
        add("roe", "净资产收益率(ROE)", fin.get("roe_pct"), "percent")
        add("eps", "基本每股收益", fin.get("eps"), "price")

        window = f"{price.get('start_date', '')}~{price.get('end_date', '')}"
        add("latest_close", "最新收盘价", price.get("latest_close"), "price", window)
        add("latest_pct", "最新单日涨跌幅", price.get("latest_pct_change"), "percent", window)
        add("period_return", "区间涨跌幅", price.get("period_return_pct"), "percent", window)
        add("period_high", "区间最高价", price.get("period_high"), "price", window)
        add("period_low", "区间最低价", price.get("period_low"), "price", window)
        add("annual_vol", "年化波动率", price.get("annual_volatility_pct"), "percent", window)
        add("avg_turnover", "平均换手率", price.get("avg_turnover_pct"), "percent", window)

        val = self.bundle.valuation
        if val:
            add("pe_ratio", "市盈率(PE-TTM)", val.get("pe"), "price", val.get("trade_date", ""))
            add("pb_ratio", "市净率(PB)", val.get("pb"), "price", val.get("trade_date", ""))
            add("dividend_yield", "股息率", val.get("dv_ratio"), "percent", val.get("trade_date", ""))

        ff = self.bundle.fund_flow
        if ff:
            add("main_net_inflow", "主力净流入净额", ff.get("main_net_inflow"), "yuan",
                ff.get("date", ""))

    # ------------------------------------------------------------------
    def recent_periods_table(self) -> str:
        rows = self.bundle.financial_summary.get("recent_periods", [])
        if not rows:
            return ""
        lines = ["| 报告期 | 营业总收入(亿元) | 归母净利润(亿元) | 营收同比 | 净利同比 | 毛利率 | ROE | EPS |",
                 "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in rows:
            lines.append(
                "| {p} | {rev} | {np} | {ry} | {ny} | {gm} | {roe} | {eps} |".format(
                    p=r.get("period", ""),
                    rev=f"{(r.get('revenue_yuan') or 0)/_YI:.2f}",
                    np=f"{(r.get('net_profit_yuan') or 0)/_YI:.2f}",
                    ry=fmt_pct(r.get("revenue_yoy_pct")),
                    ny=fmt_pct(r.get("net_profit_yoy_pct")),
                    gm=fmt_pct(r.get("gross_margin_pct"), signed=False),
                    roe=fmt_pct(r.get("roe_pct"), signed=False),
                    eps=r.get("eps") if r.get("eps") is not None else "—",
                )
            )
        return "\n".join(lines)

    def fact_pack_text(self) -> str:
        """渲染为注入给大模型的“唯一可用事实包”。"""
        b = self.bundle
        source_tag = "实时抓取(AKShare)" if b.source == "live" else "离线虚构样例(非真实证券)"
        lines = [
            f"【已核实数据包 · 你只能使用以下数字，严禁引入数据包之外的任何财务/行情数字】",
            f"公司：{b.name}　代码：{b.symbol}　数据来源：{source_tag}　截至：{b.as_of}",
            "",
            f"■ 最新财务（报告期：{self.bundle.financial_summary.get('latest_period', '—')}）",
        ]
        order = ["revenue", "revenue_yoy", "net_profit", "net_profit_yoy",
                 "gross_margin", "net_margin", "roe", "eps"]
        for key in order:
            fact = self.facts.get(key)
            if fact and fact.value is not None:
                lines.append("- " + fact.display())

        table = self.recent_periods_table()
        if table:
            lines += ["", "■ 近五期财务趋势", table]

        p = self.bundle.price_summary
        if p:
            lines += [
                "",
                f"■ 行情（{p.get('start_date')}~{p.get('end_date')}，共 {p.get('trading_days')} 个交易日）",
                f"- 最新收盘价：{p.get('latest_close')} 元（当日 {fmt_pct(p.get('latest_pct_change'))}）",
                f"- 区间涨跌幅：{fmt_pct(p.get('period_return_pct'))}；"
                f"区间最高 {p.get('period_high')} 元 / 最低 {p.get('period_low')} 元",
                f"- 年化波动率：{fmt_pct(p.get('annual_volatility_pct'), signed=False)}；"
                f"平均换手率：{fmt_pct(p.get('avg_turnover_pct'), signed=False)}；"
                f"20 日均线：{p.get('ma20')}（{p.get('price_vs_ma20') or '—'}）",
            ]

        if b.news:
            lines += ["", "■ 公开资讯（仅可转述，不得改写其中数字）"]
            for i, n in enumerate(b.news, 1):
                lines.append(f"{i}. {n.get('time', '')} {n.get('title', '')}（{n.get('media', '')}）")

        val = b.valuation
        if val:
            lines += ["", "■ 估值水平"]
            if val.get("pe") is not None:
                lines.append(f"- 市盈率 PE(TTM)：{val['pe']:.2f} 倍")
            if val.get("pb") is not None:
                lines.append(f"- 市净率 PB：{val['pb']:.2f} 倍")
            if val.get("dv_ratio") is not None:
                lines.append(f"- 股息率：{val['dv_ratio']:.2f}%")

        ff = b.fund_flow
        if ff:
            lines += ["", "■ 资金流向"]
            if ff.get("main_net_inflow") is not None:
                lines.append(f"- 主力净流入：{fmt_amount(ff['main_net_inflow'])}"
                             f"（{ff.get('date', '')}）")
        return "\n".join(lines)
