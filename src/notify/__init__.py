"""通知推送模块（3.0新增）：飞书/企业微信/Telegram webhook 推送。"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .feishu import FeishuNotifier
from .telegram import TelegramNotifier
from .wecom import WeComNotifier

__all__ = ["FeishuNotifier", "WeComNotifier", "TelegramNotifier", "push_brief_result"]


def push_brief_result(
    title: str,
    text: str,
    verification: Optional[Dict[str, Any]] = None,
    feishu_webhook_url: Optional[str] = None,
    feishu_secret: Optional[str] = None,
    wecom_webhook_key: Optional[str] = None,
    telegram_bot_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
) -> None:
    """把速览结果推送到飞书/企业微信/Telegram（按配置自动选择渠道）。

    CLI / Web / MCP 三个入口共用，不用各自重复实现。
    未配置任何推送渠道时静默跳过。
    """
    feishu_url = feishu_webhook_url or os.getenv("FEISHU_WEBHOOK_URL", "")
    wecom_key = wecom_webhook_key or os.getenv("WECOM_WEBHOOK_KEY", "")
    tg_token = telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    if not feishu_url and not wecom_key and not tg_token:
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
    if tg_token and tg_chat:
        TelegramNotifier(tg_token, tg_chat).send_markdown(title, summary)
