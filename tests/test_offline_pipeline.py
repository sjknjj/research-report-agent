"""离线模式下端到端流水线测试。"""
import unittest

from src.agent.base import BaseAgent
from src.agent.orchestrator import ResearchOrchestrator
from src.agent.researcher import ResearcherAgent
from src.common.config import load_settings
from src.common.schema import AgentContext, ReportSection, ResearchReport
from src.export.exporter import build_html, build_markdown
from src.llm.client import LLMClient


class OfflinePipelineTest(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings(
            cli_overrides={"mode": "offline", "search_provider": "mock"}
        )
        self.topic = "低空经济产业链研究"

    def _run_with_ctx(self, **run_kwargs):
        """跑一遍流水线并捕获最终的 AgentContext。"""
        captured = {}

        def on_step(name, ctx):
            captured["ctx"] = ctx

        report = ResearchOrchestrator(self.settings).run(
            self.topic, on_step=on_step, **run_kwargs
        )
        return report, captured.get("ctx")

    def test_end_to_end(self):
        report = ResearchOrchestrator(self.settings).run(self.topic)

        # 结构完整性
        self.assertEqual(report.topic, self.topic)
        self.assertGreaterEqual(len(report.sections), 5)
        self.assertTrue(report.abstract.strip())
        self.assertGreater(len(report.references), 0)

        # Markdown 渲染
        md = build_markdown(report)
        self.assertIn(self.topic, md)
        self.assertIn("摘要", md)
        self.assertIn("参考文献", md)
        self.assertIn("离线演示模式", md)  # 离线必须有显著提示
        for section in report.sections:
            self.assertIn(section.heading, md)

        # HTML 渲染为自包含页面
        html = build_html(report)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<h1", html)
        self.assertIn(self.topic, html)

    def test_review_loop_runs(self):
        self.settings.max_review_rounds = 1
        report = ResearchOrchestrator(self.settings).run(self.topic)
        self.assertTrue(report.meta.get("trace"))

    # -- 第 17 条：空方论点不得被丢弃 --------------------------------------
    def test_debate_merges_bull_and_bear(self):
        report, ctx = self._run_with_ctx()
        debate = ctx.debate
        self.assertIsNotNone(debate)
        self.assertTrue(debate.bull_points)
        self.assertTrue(debate.bear_points)
        # schema 声明的两个反驳字段必须被使用
        self.assertTrue(debate.bull_rebuttal)
        self.assertTrue(debate.bear_rebuttal)
        # to_dict 不能丢字段
        data = debate.to_dict()
        self.assertIn("bear_points", data)
        self.assertIn("bull_rebuttal", data)
        self.assertIn("bear_rebuttal", data)

        md = build_markdown(report)
        # 空方内容出现在速览仪表盘与多空对照里
        self.assertIn("离线模式：风险观点占位", md)
        self.assertNotIn("**主要风险（空方）**：\n- —", md)
        self.assertIn("空方反驳（针对多方）", md)
        # 多空对照表里不应再出现整列 "—"
        self.assertNotIn("| 论点 1 | 离线模式：乐观观点占位 | — |", md)

    # -- 第 18 条：反幻觉总览必须诚实 --------------------------------------
    def test_anti_hallucination_overview_is_honest(self):
        report, _ = self._run_with_ctx()
        md = build_markdown(report)
        # 不能出现伪造的可信度声明
        self.assertNotIn("A（高可信）", md)
        self.assertNotIn("幻觉率 < 5%", md)
        self.assertNotIn("三道防线校验", md)
        # 必须如实标注未启用与未评级
        self.assertIn("未评级", md)
        self.assertIn("未启用", md)
        self.assertIn("数字校验未启用", md)

        checks = report.meta.get("checks", {})
        self.assertIn("numeric_verify", checks)
        self.assertFalse(checks["numeric_verify"]["executed"])
        self.assertTrue(checks["structure"]["executed"])
        self.assertTrue(checks["llm_review"]["executed"])

    # -- 第 20 条：引用覆盖率非阻断 + 审校可收敛 --------------------------
    def test_citation_coverage_is_non_blocking(self):
        _, ctx = self._run_with_ctx()
        review = ctx.review
        self.assertIsNotNone(review)
        self.assertTrue(review.passed)
        self.assertFalse(
            any("引用覆盖率偏低" in issue for issue in review.issues),
            "引用覆盖率不应作为阻断问题",
        )

    # -- 第 8/6 条：离线 --deep 提示与 settings 轮数 ----------------------
    def test_offline_deep_gives_visible_hint(self):
        report, ctx = self._run_with_ctx(deep=True)
        self.assertTrue(report.meta["deep_requested"])
        self.assertFalse(report.meta["deep_effective"])
        self.assertTrue(any("深挖" in line for line in ctx.trace))

    def test_deep_rounds_come_from_settings(self):
        settings = load_settings(
            cli_overrides={"mode": "offline", "deep_search_rounds": 3}
        )
        agent = ResearcherAgent(LLMClient(settings), settings)
        self.assertEqual(agent._resolve_deep_rounds(None), 3)
        self.assertEqual(agent._resolve_deep_rounds(1), 1)  # 显式传参优先

    # -- 第 7 条：max_fetch_pages 限制尝试次数 ----------------------------
    def test_max_fetch_pages_limits_attempts(self):
        from unittest.mock import patch

        settings = load_settings(
            cli_overrides={"mode": "offline", "max_fetch_pages": 3}
        )
        agent = ResearcherAgent(LLMClient(settings), settings)

        class _Result:
            def __init__(self, i):
                self.url = f"https://example.com/{i}"
                self.fetched_content = ""

        results = [_Result(i) for i in range(10)]
        with patch("src.agent.researcher.fetch_page", return_value="") as fake:
            agent._enrich_with_fetched_content(results)
        # 失败链接不应导致尝试所有结果，只尝试 max_fetch_pages 次
        self.assertEqual(fake.call_count, 3)

    # -- 第 9 条：LLM JSON 健壮解析 ---------------------------------------
    def test_robust_coercion_helpers(self):
        self.assertEqual(BaseAgent._to_int("7.5", 7), 8)
        self.assertEqual(BaseAgent._to_int("85分", 0), 85)
        self.assertEqual(BaseAgent._to_int(None, 7), 7)
        self.assertEqual(BaseAgent._to_int("abc", 5), 5)
        self.assertIs(BaseAgent._to_bool("false", True), False)
        self.assertIs(BaseAgent._to_bool("不通过", True), False)
        self.assertIs(BaseAgent._to_bool("true", False), True)
        self.assertIs(BaseAgent._to_bool(None, True), True)

    def test_reviewer_does_not_treat_string_false_as_passed(self):
        from src.agent.reviewer import ReviewerAgent

        class _FakeLLM:
            offline = True

            def chat_json(self, messages, **kwargs):
                return {"passed": "false", "score": "85分", "issues": [], "suggestions": []}

            def chat(self, messages, **kwargs):  # pragma: no cover - 兜底
                return ""

        settings = load_settings(cli_overrides={"mode": "offline"})
        agent = ReviewerAgent(_FakeLLM(), settings)
        report = ResearchReport(
            topic="测试",
            abstract="这是一段足够长的摘要文本，用于通过结构校验。" * 2,
            sections=[
                ReportSection(
                    heading="正文",
                    content="这是一段足够长的正文内容，用于通过结构与长度校验。" * 2,
                    citations=[],
                )
            ],
            references=[],
        )
        ctx = AgentContext(topic="测试", report=report)
        agent.run(ctx)
        self.assertFalse(ctx.review.passed, "字符串 'false' 必须解析为审校不通过")
        self.assertEqual(ctx.review.score, 85)


    # -- 第 10 条：认证类错误必须上抛，瞬时错误才兜底 --------------------
    def test_fatal_auth_error_is_raised(self):
        from src.agent.planner import PlannerAgent
        from src.llm.client import LLMError

        class _AuthFailLLM:
            offline = False

            def chat_json(self, *args, **kwargs):
                raise LLMError("调用大模型失败：HTTP 401 Unauthorized；响应：invalid api key")

            def chat(self, *args, **kwargs):
                raise LLMError("调用大模型失败：HTTP 401 Unauthorized")

        settings = load_settings(
            cli_overrides={"mode": "online", "llm_api_key": "bad-key"}
        )
        agent = PlannerAgent(_AuthFailLLM(), settings)
        with self.assertRaises(LLMError):
            agent.run(AgentContext(topic="测试"))

    def test_transient_error_uses_fallback(self):
        from src.agent.planner import PlannerAgent
        from src.llm.client import LLMError

        class _TransientLLM:
            offline = False

            def chat_json(self, *args, **kwargs):
                raise LLMError("无法连接大模型服务：timed out")

            def chat(self, *args, **kwargs):
                raise LLMError("connection reset by peer")

        settings = load_settings(
            cli_overrides={"mode": "online", "llm_api_key": "x"}
        )
        agent = PlannerAgent(_TransientLLM(), settings)
        ctx = AgentContext(topic="测试")
        agent.run(ctx)
        self.assertTrue(ctx.plan.sections)  # 瞬时错误退化为内置模板


if __name__ == "__main__":
    unittest.main()
