"""LLM 客户端：离线兜底与 JSON 稳健解析测试。"""
import unittest

from src.common.config import load_settings
from src.llm.client import LLMClient


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


if __name__ == "__main__":
    unittest.main()
