#!/usr/bin/env python3
"""以 Python API 方式调用研报 Agent 的最小示例（离线模式，无需密钥）。

运行：
    python examples/demo_offline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让示例可以直接从 examples/ 目录运行
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.orchestrator import ResearchOrchestrator  # noqa: E402
from src.common.config import load_settings  # noqa: E402
from src.export.exporter import build_markdown, export_report  # noqa: E402


def main() -> None:
    # 强制离线 + 占位检索，保证任何环境都能跑通；在线时去掉这两个覆盖项即可
    settings = load_settings(
        cli_overrides={"mode": "offline", "search_provider": "mock"}
    )

    orchestrator = ResearchOrchestrator(settings)
    report = orchestrator.run("低空经济产业链研究")

    # 方式一：直接拿到 Markdown 文本
    markdown = build_markdown(report)
    print("\n报告预览（前 600 字）：\n")
    print(markdown[:600])

    # 方式二：导出 md / html 文件到 output/
    files = export_report(report, settings.output_dir, ("md", "html"))
    print("\n导出文件：")
    for path in files:
        print(" -", path)


if __name__ == "__main__":
    main()
