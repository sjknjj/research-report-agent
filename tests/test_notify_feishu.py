"""飞书机器人推送（src/notify/feishu.py）单元测试。

不联网：`requests.post` 全部被 mock。
重点：
- HMAC-SHA256 签名算法与飞书官方口径一致（key=timestamp\\nsecret，空消息，base64）；
- 未配置 webhook 时绝不发出请求。
"""
import base64
import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

from src.notify.feishu import FeishuNotifier

_POST = "src.notify.feishu.requests.post"

# 已知向量：预计算，锚定签名算法（变化即回归）
_KNOWN_SIGNATURES = {
    (1700000000, "testsecret"): "AOc8oJ7//5OlQlfWC3nRL0R+IkuzcD1FKcAyibRK9Q8=",
    (1, "a"): "rWZ84lcag1x9aBFhn1gtV4ZN+4gme3pilfQNMk86vKg=",
}


def _fake_response(body):
    resp = MagicMock()
    resp.json.return_value = body
    return resp


class FeishuSignTest(unittest.TestCase):
    def test_sign_matches_known_vectors(self):
        for (ts, secret), expected in _KNOWN_SIGNATURES.items():
            notifier = FeishuNotifier("https://example.com/hook", secret=secret)
            self.assertEqual(notifier._sign(ts), expected)

    def test_sign_matches_official_algorithm(self):
        ts, secret = 1699999999, "s3cr3t"
        string_to_sign = f"{ts}\n{secret}"
        expected = base64.b64encode(
            hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        notifier = FeishuNotifier("https://example.com/hook", secret=secret)
        self.assertEqual(notifier._sign(ts), expected)

    def test_sign_empty_without_secret(self):
        self.assertEqual(FeishuNotifier("https://example.com/hook")._sign(123), "")
        self.assertEqual(FeishuNotifier("https://example.com/hook", secret="")._sign(123), "")

    def test_sign_changes_with_timestamp_and_secret(self):
        a = FeishuNotifier("u", secret="s1")
        b = FeishuNotifier("u", secret="s2")
        self.assertNotEqual(a._sign(100), a._sign(101))
        self.assertNotEqual(a._sign(100), b._sign(100))


class FeishuSendTest(unittest.TestCase):
    def test_no_request_when_webhook_missing(self):
        notifier = FeishuNotifier("", secret="secret")
        with patch(_POST) as post:
            self.assertFalse(notifier.send_text("hello"))
            self.assertFalse(notifier.send_markdown("标题", "正文"))
            post.assert_not_called()

    def test_send_text_posts_json_payload(self):
        notifier = FeishuNotifier("https://example.com/hook")
        with patch(_POST, return_value=_fake_response({"code": 0})) as post:
            self.assertTrue(notifier.send_text("测试消息"))

        self.assertEqual(post.call_count, 1)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://example.com/hook")
        payload = json.loads(kwargs["data"])
        self.assertEqual(payload["msg_type"], "text")
        self.assertEqual(payload["content"]["text"], "测试消息")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        # 未配置 secret 时不携带签名
        self.assertNotIn("sign", payload)
        self.assertNotIn("timestamp", payload)

    def test_send_markdown_payload_structure(self):
        notifier = FeishuNotifier("https://example.com/hook")
        with patch(_POST, return_value=_fake_response({"code": 0})) as post:
            self.assertTrue(notifier.send_markdown("标题", "**正文**"))
        payload = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["header"]["title"]["content"], "标题")
        self.assertEqual(payload["card"]["elements"][0]["content"], "**正文**")

    def test_secret_adds_valid_timestamp_and_sign(self):
        notifier = FeishuNotifier("https://example.com/hook", secret="topsecret")
        with patch(_POST, return_value=_fake_response({"code": 0})) as post:
            self.assertTrue(notifier.send_text("带签名"))
        payload = json.loads(post.call_args.kwargs["data"])
        self.assertIn("timestamp", payload)
        self.assertIn("sign", payload)
        ts = int(payload["timestamp"])
        self.assertEqual(payload["sign"], notifier._sign(ts))

    def test_success_via_status_code_field(self):
        notifier = FeishuNotifier("https://example.com/hook")
        with patch(_POST, return_value=_fake_response({"StatusCode": 0})):
            self.assertTrue(notifier.send_text("ok"))

    def test_business_error_returns_false(self):
        notifier = FeishuNotifier("https://example.com/hook")
        with patch(_POST, return_value=_fake_response({"code": 19001, "msg": "sign error"})):
            self.assertFalse(notifier.send_text("bad"))

    def test_network_exception_returns_false(self):
        notifier = FeishuNotifier("https://example.com/hook")
        with patch(_POST, side_effect=RuntimeError("connection refused")):
            self.assertFalse(notifier.send_text("boom"))

    def test_non_json_response_returns_false(self):
        notifier = FeishuNotifier("https://example.com/hook")
        resp = MagicMock()
        resp.json.side_effect = ValueError("not json")
        with patch(_POST, return_value=resp):
            self.assertFalse(notifier.send_text("boom"))


if __name__ == "__main__":
    unittest.main()
