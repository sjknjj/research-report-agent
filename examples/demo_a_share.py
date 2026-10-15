#!/usr/bin/env python3
"""案例 1：A 股公司投研速览（离线样例，无需密钥/网络）。

演示：AKShare 口径数据清洗 → 事实包锁定 → 大模型生成 → 数字交叉校验 → 导出。
在线时把 prefer_live 改为 True、并在 .env 配置 LLM_API_KEY 即可分析真实公司。

运行：python examples/demo_a_share.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.config import load_settings  # noqa: E402
from src.export.exporter import export_assistant  # noqa: E402
from src.services.research_assistant import ResearchAssistant  # noqa: E402


def main() -> None:
    settings = load_settings(cli_overrides={"mode": "offline", "max_review_rounds": 1})
    assistant = ResearchAssistant(settings)

    # 离线样例公司 000000；真实使用改为如 "600519" 并 prefer_live=True
    result = assistant.company_brief("000000", prefer_live=False)

    print(result.text)
    v = result.verification
    print("\n交叉校验：",
          f"通过={v['passed']}，一致 {v['supported']}/{v['total_claims']}，"
          f"矛盾 {v['mismatch']}，幻觉率 {v['hallucination_rate']:.1%}")

    files = export_assistant(
        "演示样本公司 投研速览（案例）",
        result.text,
        settings.output_dir,
        ("md", "html"),
        verification=result.verification,
        fact_pack=result.fact_pack,
        meta_lines=["离线案例", "虚构样例数据"],
    )
    for path in files:
        print("已导出：", path)


if __name__ == "__main__":
    main()
