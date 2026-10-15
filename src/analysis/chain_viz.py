"""产业链图谱可视化（networkx + matplotlib）。

把产业链的上游/中游/下游三层公司关系画成关系网络图，
渲染成 base64 PNG，可直接嵌入 HTML 报告（无需前端依赖）。
未安装 networkx/matplotlib 时返回空字符串，不阻塞流程。
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


def _setup_font(plt):
    import matplotlib
    for name in ["PingFang SC", "Microsoft YaHei", "SimHei",
                 "WenQuanYi Zen Hei", "Arial Unicode MS", "Noto Sans CJK SC"]:
        try:
            matplotlib.rcParams["font.sans-serif"] = [name]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue


def chain_graph_html(chain_info: Optional[dict], highlight_code: str = "") -> str:
    """生成产业链关系图的 base64 内嵌 <img> HTML。"""
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

    # 相邻层公司之间连边（串行供应链关系）
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

    # 层标题
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
