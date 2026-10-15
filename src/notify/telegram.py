"""Telegram 推送（v3.4新增）。"""
from __future__ import annotations

from typing import Optional

import requests

from ..common.logger import get_logger

logger = get_logger("notify.telegram")


class TelegramNotifier:
    """Telegram Bot 推送。

    使用方法：
    1. 在 Telegram 找 @BotFather 创建 bot，拿到 token
    2. 给你的 bot 发一条消息，然后访问 https://api.telegram.org/bot<token>/getUpdates
       拿到 chat_id
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = f"https://api.telegram.org/bot{bot_token}"

    def send_markdown(self, title: str, text: str) -> bool:
        """发送 Markdown 格式消息。"""
        full_text = f"*{title}*\n\n{text}"
        try:
            resp = requests.post(
                f"{self.api_base}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": full_text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                logger.info("Telegram 推送成功")
                return True
            logger.warning("Telegram 推送失败：%s", resp.text)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram 推送异常：%s", exc)
            return False
