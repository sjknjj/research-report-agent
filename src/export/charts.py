"""财务图表生成（Plotly）。

生成可嵌入 HTML 的自包含图表 div，无需前端构建。
未安装 plotly 时所有函数返回空字符串，不阻塞主流程。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger("export.charts")

_PLOTLY_JS_TAG: Optional[str] = None


def _plotly_js_tag() -> str:
    """返回 plotly.js 的 <script> 片段，优先内联（离线可用），失败才退回 CDN。"""
    global _PLOTLY_JS_TAG
    if _PLOTLY_JS_TAG is None:
        try:
            from plotly.offline import get_plotlyjs

            _PLOTLY_JS_TAG = "<script>" + get_plotlyjs() + "</script>"
        except Exception:  # noqa: BLE001
            logger.warning("无法内联 plotly.js，退回 CDN 版本（离线环境图表将无法显示）")
            _PLOTLY_JS_TAG = (
                '<script src="https://cdn.plot.ly/plotly-2.27.0.min.js" charset="utf-8"></script>'
            )
    return _PLOTLY_JS_TAG


def ensure_plotly_js(chart_html: str) -> str:
    """保证任意图表组合下 plotly.js 只注入一次。

    chart_* 函数统一以 ``include_plotlyjs=False`` 产出纯 div + newPlot 脚本，
    由这里在汇总处前置一份内联 plotly.js，避免"只有趋势图/仪表盘时图表空白"。
    """
    if not chart_html or "Plotly.newPlot" not in chart_html:
        return chart_html
    if "cdn.plot.ly" in chart_html or "plotly.js" in chart_html:
        return chart_html
    return _plotly_js_tag() + chart_html


def _load_plotly():
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        return go, make_subplots
    except ImportError:
        return None, None


def kline_with_volume(price_df: Optional[pd.DataFrame]) -> str:
    """K线 + 成交量双联图，返回 HTML div 字符串。

    price_df 需含 date/open/close/high/low/volume 列（clean_price 输出格式）。
    """
    if price_df is None or price_df.empty:
        return ""
    go, make_subplots = _load_plotly()
    if go is None:
        logger.info("未安装 plotly，跳过 K 线图（pip install plotly 启用）")
        return ""

    df = price_df.tail(120).copy()
    if "date" not in df.columns:
        return ""

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.03,
        subplot_titles=("K线走势", "成交量"),
    )
    fig.add_trace(
        go.Candlestick(
            x=df["date"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            increasing_line_color="#d63031", decreasing_line_color="#00b894",
            name="K线",
        ),
        row=1, col=1,
    )
    if "ma20" in df.columns:
        fig.add_trace(
            go.Scatter(x=df["date"], y=df["ma20"], mode="lines",
                       name="MA20", line=dict(width=1, color="#0984e3")),
            row=1, col=1,
        )
    colors = ["#d63031" if c >= o else "#00b894"
              for c, o in zip(df["close"], df["open"])]
    if "volume" in df.columns:
        fig.add_trace(
            go.Bar(x=df["date"], y=df["volume"], marker_color=colors, name="成交量"),
            row=2, col=1,
        )

    fig.update_layout(
        height=480, showlegend=False,
        xaxis_rangeslider_visible=False,
        margin=dict(l=40, r=20, t=40, b=30),
        paper_bgcolor="white", plot_bgcolor="white",
        font=dict(family="PingFang SC, Microsoft YaHei, sans-serif", size=12),
    )
    fig.update_yaxes(title_text="价格(元)", row=1, col=1)
    fig.update_yaxes(title_text="成交量(手)", row=2, col=1)
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id="kline-chart")


def financial_trend_chart(financial_df: Optional[pd.DataFrame]) -> str:
    """近五期营收/净利润趋势柱状图。"""
    if financial_df is None or financial_df.empty:
        return ""
    go, _ = _load_plotly()
    if go is None:
        return ""

    df = financial_df.head(5).iloc[::-1]
    periods = [str(p) for p in df["period"]]
    revenue = df.get("revenue", pd.Series(dtype=float)) / 1e8
    net_profit = df.get("net_profit", pd.Series(dtype=float)) / 1e8

    fig = go.Figure()
    fig.add_trace(go.Bar(x=periods, y=revenue, name="营业总收入(亿元)",
                         marker_color="#0984e3"))
    fig.add_trace(go.Bar(x=periods, y=net_profit, name="归母净利润(亿元)",
                         marker_color="#fdcb6e"))
    fig.update_layout(
        barmode="group", height=320,
        title="近五期财务趋势",
        margin=dict(l=40, r=20, t=50, b=30),
        font=dict(family="PingFang SC, Microsoft YaHei, sans-serif", size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id="fin-trend")


def valuation_gauge(pe: Optional[float], pb: Optional[float]) -> str:
    """PE/PB 估值仪表盘。"""
    if pe is None and pb is None:
        return ""
    go, _ = _load_plotly()
    if go is None:
        return ""

    rows = []
    if pe is not None:
        rows.append(dict(type="indicator", value=pe,
                         number=dict(suffix="x"), title=dict(text="PE(TTM)")))
    if pb is not None:
        rows.append(dict(type="indicator", value=pb,
                         number=dict(suffix="x"), title=dict(text="PB")))
    fig = go.Figure(data=rows)
    fig.update_layout(
        grid=dict(rows=1, columns=len(rows)),
        height=200, margin=dict(l=30, r=30, t=30, b=10),
        font=dict(family="PingFang SC, Microsoft YaHei, sans-serif"),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id="val-gauge")
