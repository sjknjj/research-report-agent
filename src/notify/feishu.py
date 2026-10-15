"""飞书自定义机器人 webhook 推送。

使用前在飞书群里添加自定义机器人，拿到 webhook URL（含 sign）。
"""
from __future__ import annotations

import hashlib
import base64
import time
import hmac
import json
from typing import Optional

import requests

from ..common.logger import get_logger

logger = get_logger("notify.feishu")


class FeishuNotifier:
    """飞书群机器人推送（支持签名校验）。"""

    def __init__(self, webhook_url: str, secret: Optional[str] = None):
        self.webhook_url = webhook_url
        self.secret = secret

    def _sign(self, timestamp: int) -> str:
        if not self.secret:
            return ""
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    def send_text(self, text: str) -> bool:
        """发送纯文本消息。"""
        return self._send({"msg_type": "text", "content": {"text": text}})

    def send_markdown(self, title: str, markdown: str) -> bool:
        """发送 markdown 卡片消息。"""
        return self._send({
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title},
                           "template": "blue"},
                "elements": [{"tag": "markdown", "content": markdown}],
            },
        })

    def _send(self, payload: dict) -> bool:
        if not self.webhook_url:
            logger.warning("飞书 webhook 未配置，跳过推送")
            return False
        timestamp = int(time.time())
        sign = self._sign(timestamp)
        if sign:
            payload["timestamp"] = str(timestamp)
            payload["sign"] = sign
        try:
            resp = requests.post(
                self.webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == 0 or data.get("StatusCode") == 0:
                logger.info("飞书推送成功")
                return True
            logger.warning("飞书推送失败：%s", data)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("飞书推送异常：%s", exc)
            return False
