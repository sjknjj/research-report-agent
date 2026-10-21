"""LLM 客户端：离线兜底与 JSON 稳健解析测试。"""
import unittest

from src.common.config import is_usable_api_key, load_settings
from src.llm.client import LLMClient, LLMError


class LLMClientTest(unittest.TestCase):
    def setUp(self):
        settings = load_settings(
            cli_overrides={"mode": "offline", "search_provider": "mock"}
        )
        self.client = LLMClient(settings)
        self.assertTrue(self.client.offline)

    def test_offline_plan_json(self):
        data = self.client.chat_json(
            [{"role": "user", "content": "提纲"}],
            stage="plan",
            context={"topic": "储能行业"},
        )
        self.assertIn("sections", data)
        self.assertGreaterEqual(len(data["sections"]), 5)

    def test_parse_plain_json(self):
        obj = LLMClient.parse_json('{"a": 1, "b": [2, 3]}')
        self.assertEqual(obj, {"a": 1, "b": [2, 3]})

    def test_parse_fenced_json(self):
        raw = '好的，结果如下：\n```json\n{"passed": true, "score": 90}\n```\n'
        obj = LLMClient.parse_json(raw)
        self.assertTrue(obj["passed"])
        self.assertEqual(obj["score"], 90)

    def test_parse_json_with_surrounding_text(self):
        raw = '前缀文字 [{"x": 1}] 后缀文字'
        obj = LLMClient.parse_json(raw)
        self.assertEqual(obj, [{"x": 1}])


class ApiKeyUsabilityTest(unittest.TestCase):
    """占位符 / 含中文的 Key 必须在「判定模式」阶段就被识破。

    这两类 Key 会让请求在 ``http.client.putheader`` 里抛
    ``UnicodeEncodeError: 'latin-1' codec can't encode characters``，
    接口层只看到一句「调用失败」，根本看不出是 .env 没改。
    """

    def test_placeholder_key_rejected(self):
        self.assertFalse(is_usable_api_key("sk-请替换为你的密钥"))
        self.assertFalse(is_usable_api_key(""))
        self.assertFalse(is_usable_api_key("   "))
        self.assertFalse(is_usable_api_key("your_api_key_here"))
        self.assertFalse(is_usable_api_key("sk-changeme"))

    def test_real_key_accepted(self):
        self.assertTrue(is_usable_api_key("sk-abcdef1234567890"))
        self.assertTrue(is_usable_api_key("  sk-abcdef1234567890  "))

    def test_non_ascii_key_rejected(self):
        # 任何一个中文字符都足以让 HTTP 头部编码失败
        self.assertFalse(is_usable_api_key("sk-abc中文def"))

    def test_auto_mode_falls_back_to_offline_with_placeholder(self):
        settings = load_settings(
            cli_overrides={"mode": "auto", "llm_api_key": "sk-请替换为你的密钥"}
        )
        self.assertFalse(settings.api_key_usable)
        self.assertEqual(settings.resolve_effective_mode(), "offline")

    def test_auto_mode_online_with_real_key(self):
        settings = load_settings(
            cli_overrides={"mode": "auto", "llm_api_key": "sk-abcdef1234567890"}
        )
        self.assertTrue(settings.api_key_usable)
        self.assertEqual(settings.resolve_effective_mode(), "online")

    def test_explicit_online_mode_raises_readable_error(self):
        settings = load_settings(
            cli_overrides={"mode": "online", "llm_api_key": "sk-请替换为你的密钥"}
        )
        client = LLMClient(settings)
        self.assertFalse(client.offline)
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        message = str(ctx.exception)
        # 报错要直接指向病因，而不是一句 latin-1
        self.assertIn("LLM_API_KEY 不可用", message)
        self.assertIn("占位符", message)


if __name__ == "__main__":
    unittest.main()
