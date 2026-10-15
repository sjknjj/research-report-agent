"""企业微信群机器人 webhook 推送。

使用前在企业微信群里添加机器人，拿到 webhook key。
"""
from __future__ import annotations

import json

import requests

from ..common.logger import get_logger

logger = get_logger("notify.wecom")


class WeComNotifier:
    """企业微信群机器人推送。"""

    def __init__(self, webhook_key: str):
        self.webhook_key = webhook_key
        self.base_url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="

    def send_text(self, text: str) -> bool:
        """发送纯文本。"""
        return self._send({"msgtype": "text", "text": {"content": text}})

    def send_markdown(self, title: str, markdown: str) -> bool:
        """发送 markdown 消息（企业微信支持有限）。

        签名与 :class:`FeishuNotifier` 保持一致：``send_markdown(title, markdown)``。
        """
        content = f"**{title}**\n\n{markdown}" if title else markdown
        return self._send({"msgtype": "markdown", "markdown": {"content": content}})

    def _send(self, payload: dict) -> bool:
        if not self.webhook_key:
            logger.warning("企业微信 webhook 未配置，跳过推送")
            return False
        url = self.base_url + self.webhook_key
        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            data = resp.json()
            if data.get("errcode") == 0:
                logger.info("企业微信推送成功")
                return True
            logger.warning("企业微信推送失败：%s", data)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("企业微信推送异常：%s", exc)
            return False
