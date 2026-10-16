"""产业链图谱可视化（Plotly 交互式）。

把产业链的上游/中游/下游三层公司关系画成交互式网络图，
支持鼠标悬停查看公司详情、点击高亮上下游关系。
未安装 plotly 时回退到 networkx 静态图，再不行就返回空字符串。
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Optional

logger = logging.getLogger("analysis.chain_viz")

LAYER_COLORS = {
    "upstream": "#74b9ff",
    "midstream": "#fab1a0",
    "downstream": "#55efc4",
}
LAYER_LABEL = {"upstream": "上游", "midstream": "中游", "downstream": "下游"}


_CJK_FONT_CANDIDATES = [
    "PingFang SC", "Microsoft YaHei", "SimHei", "WenQuanYi Zen Hei",
    "Arial Unicode MS", "Noto Sans CJK SC", "Noto Sans CJK JP",
    "Source Han Sans SC", "Heiti SC", "STHeiti",
]


def _setup_font(plt) -> bool:
    """挑选一个实际存在的中文字体。

    旧实现只是把候选名赋给 rcParams——赋值本身不会抛异常，
    所以 `try/except` 永远不会触发，离线/异构系统上会静默退回默认字体、
    中文变成方块。这里改为先用 font_manager 检查字体是否真的可用。
    """
    import matplotlib
    try:
        from matplotlib import font_manager
        available = {f.name for f in font_manager.fontManager.ttflist}
    except Exception:  # noqa: BLE001
        available = set()

    for name in _CJK_FONT_CANDIDATES:
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return True

    logger.warning(
        "未找到可用中文字体（候选：%s），静态产业链图的中文可能显示为方块",
        "、".join(_CJK_FONT_CANDIDATES),
    )
    matplotlib.rcParams["axes.unicode_minus"] = False
    return False


def chain_graph_html(chain_info: Optional[dict], highlight_code: str = "") -> str:
    """生成产业链关系图的交互式 HTML（优先 Plotly，回退静态图）。"""
    if not chain_info:
        return ""

    # 优先用 Plotly 交互式图
    try:
        import plotly.graph_objects as go
    except ImportError:
        logger.info("未安装 plotly，回退静态产业链图")
        return _static_chain_graph(chain_info, highlight_code)

    # Plotly 交互式图
    nodes = []
    edges_x = []
    edges_y = []
    layer_order = ["upstream", "midstream", "downstream"]
    x_pos = {"upstream": 0, "midstream": 1, "downstream": 2}

    for layer in layer_order:
        comps = chain_info.get(layer, [])
        n = len(comps)
        for idx, comp in enumerate(comps):
            y = (n - 1) / 2 - idx if n else 0
            nodes.append({
                "name": comp["name"],
                "code": comp.get("code", ""),
                "x": x_pos[layer],
                "y": y,
                "layer": layer,
                "color": LAYER_COLORS[layer],
                "size": 30 if comp.get("code") == highlight_code else 20,
            })

    # 相邻层连边（用 name -> node 映射，避免 O(n²) 的 next() 查找 + 找不到时抛 StopIteration）
    node_by_name = {n["name"]: n for n in nodes}
    for a, b in zip(layer_order, layer_order[1:]):
        for c1 in chain_info.get(a, []):
            for c2 in chain_info.get(b, []):
                n1 = node_by_name.get(c1.get("name"))
                n2 = node_by_name.get(c2.get("name"))
                if n1 is None or n2 is None:
                    continue
                edges_x += [n1["x"], n2["x"], None]
                edges_y += [n1["y"], n2["y"], None]

    # 画边
    edge_trace = go.Scatter(
        x=edges_x, y=edges_y,
        line=dict(width=1, color="#b2bec3"),
        hoverinfo="none",
        mode="lines",
    )

    # 画节点（按层分组，方便图例）
    node_traces = []
    for layer in layer_order:
        layer_nodes = [n for n in nodes if n["layer"] == layer]
        node_traces.append(go.Scatter(
            x=[n["x"] for n in layer_nodes],
            y=[n["y"] for n in layer_nodes],
            mode="markers+text",
            name=LAYER_LABEL[layer],
            text=[n["name"] for n in layer_nodes],
            textposition="middle right",
            hovertemplate="<b>%{text}</b><br>代码: %{customdata[0]}<br>层级: " + LAYER_LABEL[layer] + "<extra></extra>",
            customdata=[[n["code"]] for n in layer_nodes],
            marker=dict(
                size=[n["size"] for n in layer_nodes],
                color=LAYER_COLORS[layer],
                line=dict(width=2, color="#2d3436"),
            ),
        ))

    fig = go.Figure(
        data=[edge_trace] + node_traces,
        layout=go.Layout(
            # 注意：新版本 plotly 已移除 `titlefont_size`，改用 title.font.size；
            # 旧写法会让有产业链的标的（如 600519）在生成速览时直接抛 ValueError。
            title=dict(text=f"{chain_info['chain']}产业链图谱（交互式）",
                       font=dict(size=16)),
            showlegend=True,
            hovermode="closest",
            margin=dict(b=20, l=5, r=5, t=40),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            annotations=[
                dict(
                    text=LAYER_LABEL[layer],
                    showarrow=False,
                    x=x_pos[layer],
                    y=max((len(chain_info.get(layer, [])) - 1) / 2, 0) + 0.8,
                    xanchor="center",
                    font=dict(size=14, color="#2d3436"),
                )
                for layer in layer_order
            ],
        ),
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def _static_chain_graph(chain_info: Optional[dict], highlight_code: str = "") -> str:
    """回退用 networkx 静态图。"""
    if not chain_info:
        return ""
    try:
        import networkx as nx
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.info("未安装 networkx/matplotlib，跳过产业链关系图")
        return ""

    _setup_font(plt)
    G = nx.DiGraph()
    pos = {}
    node_colors = []
    node_sizes = []
    layer_order = ["upstream", "midstream", "downstream"]
    x_pos = {"upstream": 0, "midstream": 1, "downstream": 2}

    for layer in layer_order:
        comps = chain_info.get(layer, [])
        n = len(comps)
        for idx, comp in enumerate(comps):
            node_id = comp["name"]
            G.add_node(node_id)
            y = (n - 1) / 2 - idx if n else 0
            pos[node_id] = (x_pos[layer], y)
            node_colors.append(LAYER_COLORS[layer])
            node_sizes.append(1800 if comp["code"] == highlight_code else 1100)

    for a, b in zip(layer_order, layer_order[1:]):
        for c1 in chain_info.get(a, []):
            for c2 in chain_info.get(b, []):
                G.add_edge(c1["name"], c2["name"])

    fig, ax = plt.subplots(figsize=(11, 5.2))
    nx.draw_networkx_edges(G, pos, ax=ax, edge_color="#b2bec3",
                           arrows=True, arrowsize=12, width=1.0, alpha=0.6)
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color=node_colors,
                           node_size=node_sizes, edgecolors="#2d3436", linewidths=1.2)
    labels = {n: n for n in G.nodes}
    nx.draw_networkx_labels(G, pos, labels=labels, ax=ax, font_size=9)

    for layer, x in x_pos.items():
        max_y = max((len(chain_info.get(layer, [])) - 1) / 2, 0)
        ax.text(x, max_y + 0.7, LAYER_LABEL[layer],
                ha="center", fontsize=13, fontweight="bold", color="#2d3436")

    ax.set_title(f"{chain_info['chain']}产业链图谱", fontsize=14, fontweight="bold")
    ax.axis("off")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'<div class="chain-graph"><img src="data:image/png;base64,{b64}" style="max-width:100%;"/></div>'
