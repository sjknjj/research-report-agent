#!/usr/bin/env python3
"""案例 2：交叉校验如何拦截大模型“数字幻觉”（本项目反幻觉核心演示）。

做法：用清洗后的真实/样例数据构建 FactBase，再让 CrossVerifier 逐字核对
模型草稿中的每个财务数字。这里故意写一段含 3 处错误数字的草稿，
观察校验器如何精确定位并给出“正确值纠错清单”。

运行：python examples/demo_cross_verification.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.data_service import DataService  # noqa: E402
from src.verify.facts import FactBase  # noqa: E402
from src.verify.verifier import CrossVerifier  # noqa: E402


def main() -> None:
    bundle = DataService().get_company_bundle("000000", prefer_live=False)
    fact_base = FactBase(bundle)

    print("===== 喂给大模型的『已核实事实包』（节选）=====")
    print(fact_base.fact_pack_text()[:600])

    # 模拟大模型输出：其中 182.30 亿、15.20%、50.00% 三处与事实不符（典型幻觉）
    model_draft = (
        "公司2025年营业总收入182.30亿元，同比增长12.50%；归母净利润31.80亿元，"
        "同比增长15.20%；销售毛利率50.00%，净资产收益率15.60%，基本每股收益1.27元。"
    )
    print("\n===== 模拟大模型草稿（含 3 处错误数字）=====")
    print(model_draft)

    report = CrossVerifier().verify(model_draft, fact_base)
    print("\n===== 交叉校验结果 =====")
    print(report.render_text())

    print("\n结论：")
    print(f"- 共 {len(report.claims)} 处数值声明，矛盾 {report.n_mismatch} 处，"
          f"疑似幻觉率 {report.hallucination_rate:.1%}")
    print("- 这些纠错值会被自动回填给大模型重写，直到数字与数据完全一致。")


if __name__ == "__main__":
    main()
