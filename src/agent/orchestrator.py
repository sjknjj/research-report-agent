"""研报流水线编排器：规划→检索→分析→多空辩论→撰写→审校→(修订)（3.0新增辩论环节）。"""
from __future__ import annotations

import time
from typing import Callable, Optional

from ..common.config import Settings
from ..common.logger import get_logger
from ..common.schema import AgentContext, ResearchReport
from ..llm.client import LLMClient
from ..tools.search import build_search_tool
from .analyst import AnalystAgent
from .debate import DebateAgent
from .planner import PlannerAgent
from .researcher import ResearcherAgent
from .reviewer import ReviewerAgent
from .writer import WriterAgent

logger = get_logger("orchestrator")


class ResearchOrchestrator:
    """研报生成流水线。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm = LLMClient(settings)
        self.search_tool = build_search_tool(settings)
        self.planner = PlannerAgent(self.llm, settings)
        self.researcher = ResearcherAgent(self.llm, settings)
        self.analyst = AnalystAgent(self.llm, settings)
        self.debater = DebateAgent(self.llm, settings)   # 3.0：多空辩论
        self.writer = WriterAgent(self.llm, settings)
        self.reviewer = ReviewerAgent(self.llm, settings)

    def run(
        self,
        topic: str,
        on_step: Optional[Callable[[str, AgentContext], None]] = None,
        deep: bool = False,
    ) -> ResearchReport:
        """执行完整流水线并返回研报。

        :param topic: 研究主题
        :param on_step: 每个阶段结束后的回调（便于上层展示进度）
        :param deep: 是否启用递归深挖检索（多轮补检）
        """
        started = time.time()
        ctx = AgentContext(topic=topic)
        mode_tag = "在线" if self.settings.effective_mode == "online" else "离线演示"
        logger.info("========== 开始生成研报（%s模式）主题：%s ==========", mode_tag, topic)

        if deep and self.settings.is_offline:
            hint = "离线演示模式下 --deep（递归深挖检索）不可用，已按单轮检索执行。"
            logger.warning(hint)
            ctx.log(f"[orchestrator] {hint}")

        self._step("① 规划提纲", self.planner, ctx, on_step)
        self._step("② 检索资料", self.researcher, ctx, on_step,
                   with_search=True, deep=deep)
        self._step("③ 逐章分析", self.analyst, ctx, on_step)
        self._step("④ 多空辩论", self.debater, ctx, on_step)   # 3.0新增
        self._step("⑤ 撰写初稿", self.writer, ctx, on_step)

        # 审校—修订闭环
        max_rounds = max(0, self.settings.max_review_rounds)
        for round_index in range(max_rounds + 1):
            self._step(f"⑥ 审校（第{round_index + 1}轮）", self.reviewer, ctx, on_step)
            if ctx.review and ctx.review.passed:
                break
            if round_index >= max_rounds:
                logger.warning("已达最大修订轮数，保留当前版本（问题：%s", ctx.review.issues if ctx.review else "-")
                break
            self.writer.revise(
                ctx,
                (ctx.review.suggestions if ctx.review else [])
                or (ctx.review.issues if ctx.review else []),
            )
            if on_step:
                on_step("修订", ctx)

        # 审校结束后，用真实执行过的校验刷新「反幻觉总览」，
        # 避免报告中出现未经执行的可信度声明。
        self.writer.refresh_checks(ctx)

        elapsed = time.time() - started
        ctx.log(f"[orchestrator] 全流程耗时 {elapsed:.1f}s")
        logger.info("========== 研报生成完成，耗时 %.1fs ==========", elapsed)
        ctx.report.meta["elapsed_seconds"] = round(elapsed, 1)
        ctx.report.meta["trace"] = ctx.trace
        ctx.report.meta["deep_requested"] = deep
        ctx.report.meta["deep_effective"] = bool(deep and not self.settings.is_offline)
        return ctx.report

    def _step(self, name, agent, ctx, on_step, with_search: bool = False,
              deep: bool = False) -> None:
        step_start = time.time()
        logger.info("---- %s 开始 ----", name)
        if with_search:
            agent.run(
                ctx,
                self.search_tool,
                deep=deep,
                deep_rounds=self.settings.deep_search_rounds,
            )
        else:
            agent.run(ctx)
        logger.info("---- %s 完成，用时 %.1fs ----", name, time.time() - step_start)
        if on_step:
            on_step(name, ctx)
