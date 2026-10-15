#!/usr/bin/env python3
"""案例 3：公告要点提炼，并与财报数据交叉校验。

运行：python examples/demo_announcement.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.config import load_settings  # noqa: E402
from src.data import sample_data  # noqa: E402
from src.export.exporter import export_assistant  # noqa: E402
from src.services.research_assistant import ResearchAssistant  # noqa: E402


def main() -> None:
    settings = load_settings(cli_overrides={"mode": "offline"})
    assistant = ResearchAssistant(settings)

    announcement = sample_data.sample_announcement_text()
    result = assistant.extract_announcement(
        announcement,
        title="2025年年度业绩快报",
        symbol="000000",          # 提供代码后，会把公告数字与财报交叉校验
        prefer_live=False,
    )

    print(result.text)
    if result.verification:
        v = result.verification
        print("\n公告↔财报交叉校验：",
              f"通过={v['passed']}，一致 {v['supported']}、矛盾 {v['mismatch']}、待核 {v['unverifiable']}")

    files = export_assistant(
        "公告要点：2025年年度业绩快报（案例）",
        result.text,
        settings.output_dir,
        ("md",),
        verification=result.verification,
        meta_lines=["离线案例"],
    )
    for path in files:
        print("已导出：", path)


if __name__ == "__main__":
    main()
