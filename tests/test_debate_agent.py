"""多空辩论智能体（src/agent/debate.py）离线单元测试。

重点关注历史 bug：
- 空方论点/反驳曾被整块丢弃（只保留多方）；
- README 承诺两轮互相反驳，但第 2 轮字段缺失/未被执行；
- 模型返回非法 JSON 时必须兜底而不是中断流水线。
"""
import unittest

from src.agent.debate import DebateAgent
from src.common.config import load_settings
from src.common.schema import AgentContext, AnalysisNote, DebateResult
from src.llm.client import LLMClient


def _make_settings(debate_rounds=2):
    settings = load_settings(cli_overrides={"mode": "offline", "search_provider": "mock"})
    settings.debate_rounds = debate_rounds
    return settings


def _make_ctx():
    return AgentContext(
        topic="锂电池产业链研究",
        notes=[
            AnalysisNote(heading="行业背景",
                         bullet_points=["需求持续增长", "产能逐步释放"]),
            AnalysisNote(heading="风险提示", bullet_points=["价格战加剧"]),
        ],
    )


class _ScriptedLLM:
    """按 stage 返回预设结果的假客户端，并记录调用过的 stage。"""

    offline = True

    def __init__(self, responses):
        self._responses = responses
        self.stages = []

    def chat_json(self, messages, stage=None, **kwargs):
        self.stages.append(stage)
        value = self._responses.get(stage, {})
        if isinstance(value, Exception):
            raise value
        return value

    def chat(self, messages, **kwargs):
        return "综合平衡观点：多空力量较为均衡。"


class _RawStringLLM:
    """始终返回非 dict 的字符串，模拟模型输出非法 JSON。"""

    offline = True

    def __init__(self):
        self.stages = []

    def chat_json(self, messages, stage=None, **kwargs):
        self.stages.append(stage)
        return "这不是 JSON"

    def chat(self, messages, **kwargs):
        return "离线综合观点"


class DebateAgentTest(unittest.TestCase):
    # -- 离线端到端 -------------------------------------------------------
    def test_offline_run_merges_bull_and_bear(self):
        settings = _make_settings(debate_rounds=2)
        ctx = _make_ctx()
        DebateAgent(LLMClient(settings), settings).run(ctx)

        debate = ctx.debate
        self.assertIsInstance(debate, DebateResult)
        self.assertTrue(debate.bull_points, "多方论点不得为空")
        self.assertTrue(debate.bear_points, "空方论点不得被丢弃")
        self.assertNotEqual(debate.bull_points, debate.bear_points)

        # 第 1 轮反驳：离线占位也必须填充
        self.assertTrue(debate.bull_rebuttal)
        self.assertTrue(debate.bear_rebuttal)
        # 第 2 轮反驳：独立字段且被填充
        self.assertTrue(debate.bull_rebuttal_round2)
        self.assertTrue(debate.bear_rebuttal_round2)
        self.assertEqual(debate.debate_rounds, 2)
        self.assertTrue(debate.synthesis)

        self.assertGreaterEqual(debate.score, 0)
        self.assertLessEqual(debate.score, 10)

        # to_dict 不能丢字段
        data = debate.to_dict()
        for key in ("bull_points", "bear_points", "bull_rebuttal", "bear_rebuttal",
                    "bull_rebuttal_round2", "bear_rebuttal_round2", "debate_rounds"):
            self.assertIn(key, data)

        self.assertTrue(any("debate" in line for line in ctx.trace))

    def test_offline_scores_come_from_placeholders(self):
        settings = _make_settings(2)
        ctx = _make_ctx()
        DebateAgent(LLMClient(settings), settings).run(ctx)
        # 离线兜底：多方 6 分、空方 4 分 → 综合 (6-4)/2+5 = 6
        self.assertEqual(ctx.debate.score, 6)

    # -- 轮数控制 ---------------------------------------------------------
    def test_two_rounds_fill_round2_fields(self):
        llm = _ScriptedLLM({
            "bull_view": {"points": ["多方论据A"], "score": 8},
            "bear_view": {"points": ["空方论据B"], "score": 2},
            "rebuttal": {"rebuttal": "第1轮反驳内容"},
            "rebuttal_round2": {"rebuttal": "第2轮再反驳内容"},
        })
        settings = _make_settings(2)
        ctx = _make_ctx()
        DebateAgent(llm, settings).run(ctx)

        debate = ctx.debate
        self.assertEqual(debate.bull_points, ["多方论据A"])
        self.assertEqual(debate.bear_points, ["空方论据B"])
        self.assertEqual(debate.bull_rebuttal, "第1轮反驳内容")
        self.assertEqual(debate.bear_rebuttal, "第1轮反驳内容")
        self.assertEqual(debate.bull_rebuttal_round2, "第2轮再反驳内容")
        self.assertEqual(debate.bear_rebuttal_round2, "第2轮再反驳内容")
        self.assertEqual(debate.debate_rounds, 2)
        # 多方 8、空方 2 → (8-2)/2+5 = 8
        self.assertEqual(debate.score, 8)

        self.assertEqual(llm.stages.count("bull_view"), 1)
        self.assertEqual(llm.stages.count("bear_view"), 1)
        self.assertEqual(llm.stages.count("rebuttal"), 2)
        self.assertEqual(llm.stages.count("rebuttal_round2"), 2)

    def test_one_round_skips_round2(self):
        llm = _ScriptedLLM({
            "bull_view": {"points": ["多方论据A"], "score": 7},
            "bear_view": {"points": ["空方论据B"], "score": 3},
            "rebuttal": {"rebuttal": "仅第1轮"},
            "rebuttal_round2": {"rebuttal": "不应被调用"},
        })
        settings = _make_settings(debate_rounds=1)
        ctx = _make_ctx()
        DebateAgent(llm, settings).run(ctx)

        debate = ctx.debate
        self.assertEqual(debate.debate_rounds, 1)
        self.assertTrue(debate.bull_rebuttal)
        self.assertTrue(debate.bear_rebuttal)
        self.assertEqual(debate.bull_rebuttal_round2, "")
        self.assertEqual(debate.bear_rebuttal_round2, "")
        self.assertNotIn("rebuttal_round2", llm.stages)
        self.assertEqual(llm.stages.count("rebuttal"), 2)

    def test_resolve_rounds_clamps_and_defaults(self):
        settings = _make_settings(2)
        agent = DebateAgent(_ScriptedLLM({}), settings)

        settings.debate_rounds = 2
        self.assertEqual(agent._resolve_rounds(), 2)
        settings.debate_rounds = 1
        self.assertEqual(agent._resolve_rounds(), 1)
        settings.debate_rounds = 0
        self.assertEqual(agent._resolve_rounds(), 1)
        settings.debate_rounds = -3
        self.assertEqual(agent._resolve_rounds(), 1)
        settings.debate_rounds = 5
        self.assertEqual(agent._resolve_rounds(), 2)
        settings.debate_rounds = "2"
        self.assertEqual(agent._resolve_rounds(), 2)

    # -- 非法 JSON 兜底 ---------------------------------------------------
    def test_illegal_json_falls_back_without_raising(self):
        llm = _RawStringLLM()
        settings = _make_settings(2)
        ctx = _make_ctx()
        DebateAgent(llm, settings).run(ctx)  # 不应抛异常

        debate = ctx.debate
        self.assertTrue(debate.bull_points)
        self.assertTrue(debate.bear_points)
        self.assertTrue(debate.bull_rebuttal)
        self.assertTrue(debate.bull_rebuttal_round2)
        self.assertIn("bull_view", llm.stages)
        self.assertIn("bear_view", llm.stages)
        self.assertIn("rebuttal_round2", llm.stages)

    def test_transient_llm_error_uses_fallback(self):
        from src.llm.client import LLMError

        llm = _ScriptedLLM({
            "bull_view": LLMError("无法连接大模型服务：timed out"),
            "bear_view": LLMError("connection reset"),
            "rebuttal": LLMError("timed out"),
            "rebuttal_round2": LLMError("timed out"),
        })
        settings = _make_settings(2)
        ctx = _make_ctx()
        DebateAgent(llm, settings).run(ctx)
        self.assertTrue(ctx.debate.bull_points)
        self.assertTrue(ctx.debate.bear_points)

    def test_missing_notes_raises(self):
        settings = _make_settings(2)
        ctx = AgentContext(topic="空上下文")
        with self.assertRaises(RuntimeError):
            DebateAgent(LLMClient(settings), settings).run(ctx)

    # -- 评分公式 ---------------------------------------------------------
    def test_final_score_formula(self):
        self.assertEqual(DebateAgent._final_score(8, 2), 8)
        self.assertEqual(DebateAgent._final_score(2, 8), 2)
        self.assertEqual(DebateAgent._final_score(5, 5), 5)
        self.assertEqual(DebateAgent._final_score(10, 0), 10)
        self.assertEqual(DebateAgent._final_score(0, 10), 0)
        # 越界输入被裁剪到 0-10
        self.assertGreaterEqual(DebateAgent._final_score(100, 0), 0)
        self.assertLessEqual(DebateAgent._final_score(100, 0), 10)

    def test_format_notes_includes_headings_and_bullets(self):
        text = DebateAgent._format_notes(_make_ctx())
        self.assertIn("行业背景", text)
        self.assertIn("需求持续增长", text)
        self.assertIn("价格战加剧", text)


if __name__ == "__main__":
    unittest.main()
