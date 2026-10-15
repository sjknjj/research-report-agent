"""Telegram 推送（v3.4新增）。"""
from __future__ import annotations


import requests

from ..common.logger import get_logger

logger = get_logger("notify.telegram")

# Telegram MarkdownV2 需要转义的特殊字符
_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!"


def _escape_md_v2(text: str) -> str:
    """转义 MarkdownV2 特殊字符，避免 400 can't parse entities。"""
    return "".join("\\" + ch if ch in _MDV2_SPECIAL else ch for ch in str(text or ""))


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
        """发送 MarkdownV2 消息（标题加粗，正文按 MarkdownV2 转义）。"""
        full_text = f"*{_escape_md_v2(title)}*\n\n{_escape_md_v2(text)}"
        try:
            resp = requests.post(
                f"{self.api_base}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": full_text,
                    "parse_mode": "MarkdownV2",
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
