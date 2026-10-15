"""通知推送模块（3.0新增）：飞书/企业微信 webhook 推送。"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .feishu import FeishuNotifier
from .wecom import WeComNotifier

__all__ = ["FeishuNotifier", "WeComNotifier", "push_brief_result"]


def push_brief_result(
    title: str,
    text: str,
    verification: Optional[Dict[str, Any]] = None,
    feishu_webhook_url: Optional[str] = None,
    feishu_secret: Optional[str] = None,
    wecom_webhook_key: Optional[str] = None,
) -> None:
    """把速览结果推送到飞书/企业微信（按配置自动选择渠道）。

    CLI / Web / MCP 三个入口共用，不用各自重复实现。
    未配置任何 webhook 时静默跳过。
    """
    feishu_url = feishu_webhook_url or os.getenv("FEISHU_WEBHOOK_URL", "")
    wecom_key = wecom_webhook_key or os.getenv("WECOM_WEBHOOK_KEY", "")

    if not feishu_url and not wecom_key:
        return

    v = verification or {}
    summary = (
        f"**{title}**\n\n"
        f"- 交叉校验：{'通过' if v.get('passed') else '未通过'}"
        f"（一致{v.get('supported', 0)}、矛盾{v.get('mismatch', 0)}）\n"
        f"- 幻觉率：{v.get('hallucination_rate', 0):.1%}\n\n"
        f"{text[:800]}..."
    )

    if feishu_url:
        secret = feishu_secret or os.getenv("FEISHU_SECRET")
        FeishuNotifier(feishu_url, secret).send_markdown(title, summary)
    if wecom_key:
        WeComNotifier(wecom_key).send_markdown(title, summary)
