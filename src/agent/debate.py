"""多空辩论智能体（3.0新增）：Bull vs Bear 对抗分析，避免单边视角。

流程：
1. BullAgent 从乐观视角提炼 3-5 个核心论据（增长、催化、估值优势）
2. BearAgent 从谨慎视角提炼 3-5 个核心论据（风险、不确定性、估值压力）
3. 双方各看对方论据做一轮反驳
4. 综合成平衡观点，给出 0-10 的多空评分
"""
from __future__ import annotations

from typing import List

from ..common.logger import get_logger
from ..common.schema import AgentContext, DebateResult
from ..llm.client import LLMError
from .base import BaseAgent

_BULL_SYSTEM = (
    "你是乐观派研究员（Bull）。你的任务是从已有的分析要点中，提炼 3-5 个最有力的"
    "看多论据，每个论据一句话、基于已有资料，不要编造。严格输出 JSON："
    '{"points": string[], "score": number(0-10)}。'
)

_BEAR_SYSTEM = (
    "你是谨慎派研究员（Bear）。你的任务是从已有的分析要点中，提炼 3-5 个最值得警惕的"
    "风险/看空论据，每个论据一句话、基于已有资料，不要编造。严格输出 JSON："
    '{"points": string[], "score": number(0-10)}。'
)

_REBUTTAL_SYSTEM = (
    "你是辩论选手。以下是对方的核心论点，请针对其中最强的 2-3 点做简短反驳"
    "（每点一两句），指出其论证漏洞或被忽略的反证。严格输出 JSON："
    '{"rebuttal": string}。'
)

_SYNTHESIS_SYSTEM = (
    "你是研究主管。以下是乐观派和谨慎派的论点与互相反驳，请做一段 150-200 字的"
    "综合平衡判断，明确当前多空力量对比、关键观察变量。直接输出文字，不要JSON。"
)


class DebateAgent(BaseAgent):
    """多空辩论智能体。"""

    role_name = "debate"

    def run(self, ctx: AgentContext, search_tool=None) -> None:
        if not ctx.notes:
            raise RuntimeError("辩论阶段缺少分析要点")

        digest = self._format_notes(ctx)
        self.logger.info("开始多空辩论，基于 %d 个章节的分析要点", len(ctx.notes))

        bull = self._bull_view(ctx.topic, digest)
        bear = self._bear_view(ctx.topic, digest)
        self.logger.info("多方 %d 个论点，空方 %d 个论点",
                         len(bull.bull_points), len(bear.bear_points))

        bull.rebuttal = self._rebuttal(ctx.topic, "乐观派", bear.bear_points)
        bear.rebuttal = self._rebuttal(ctx.topic, "谨慎派", bull.bull_points)
        self.logger.info("双方反驳完成")

        bull.synthesis = self._synthesis(ctx.topic, bull, bear)
        bull.score = self._final_score(bull.score, bear.score)
        self.logger.info("辩论综合评分：%d/10", bull.score)

        ctx.debate = bull
        ctx.log(f"[debate] 多空辩论完成（评分 {bull.score}/10）")

    # ------------------------------------------------------------------
    def _bull_view(self, topic: str, digest: str) -> DebateResult:
        try:
            data = self.llm.chat_json(
                [self._system(_BULL_SYSTEM),
                 self._user(f"研究主题：{topic}\n分析要点：\n{digest}")],
                stage="bull_view", temperature=0.3,
            )
            return DebateResult(
                bull_points=[str(p) for p in data.get("points", []) if p],
                score=int(data.get("score", 7)),
            )
        except (LLMError, ValueError, TypeError, AttributeError):
            return DebateResult(
                bull_points=["离线模式：乐观观点占位"],
                score=6,
            )

    def _bear_view(self, topic: str, digest: str) -> DebateResult:
        try:
            data = self.llm.chat_json(
                [self._system(_BEAR_SYSTEM),
                 self._user(f"研究主题：{topic}\n分析要点：\n{digest}")],
                stage="bear_view", temperature=0.3,
            )
            return DebateResult(
                bear_points=[str(p) for p in data.get("points", []) if p],
                score=int(data.get("score", 4)),
            )
        except (LLMError, ValueError, TypeError, AttributeError):
            return DebateResult(
                bear_points=["离线模式：风险观点占位"],
                score=4,
            )

    def _rebuttal(self, topic: str, opponent_role: str, opponent_points: List[str]) -> str:
        if not opponent_points:
            return ""
        points_text = "\n".join(f"- {p}" for p in opponent_points)
        try:
            data = self.llm.chat_json(
                [self._system(_REBUTTAL_SYSTEM),
                 self._user(f"对方（{opponent_role}）论点：\n{points_text}")],
                stage="rebuttal", temperature=0.2,
            )
            return str(data.get("rebuttal", ""))
        except (LLMError, ValueError, TypeError, AttributeError):
            return "（离线模式：反驳占位）"

    def _synthesis(self, topic: str, bull: DebateResult, bear: DebateResult) -> str:
        prompt = (
            f"研究主题：{topic}\n\n"
            f"乐观派论点：\n" + "\n".join(f"- {p}" for p in bull.bull_points) +
            f"\n乐观派反驳空方：{bull.rebuttal}\n\n"
            f"谨慎派论点：\n" + "\n".join(f"- {p}" for p in bear.bear_points) +
            f"\n谨慎派反驳多方：{bear.rebuttal}\n\n"
            "请综合平衡判断。"
        )
        try:
            return self.llm.chat(
                [self._system(_SYNTHESIS_SYSTEM), self._user(prompt)],
                stage="debate_synthesis", temperature=0.4,
            )
        except LLMError:
            return "（离线模式：综合观点占位）"

    @staticmethod
    def _final_score(bull_score: int, bear_score: int) -> int:
        # bull_score 是多方自评分（0-10），bear_score 是空方自评分（0-10）
        # 转换成统一的多空评分：越高越偏多
        # 多方7分 = 偏多；空方4分 = 偏空（空方4分表示空方觉得4成可能下跌）
        # 综合：(bull_score - bear_score) / 2 + 5  → 5为中性
        raw = (bull_score - bear_score) / 2 + 5
        return max(0, min(10, int(round(raw))))

    @staticmethod
    def _format_notes(ctx: AgentContext) -> str:
        lines = []
        for note in ctx.notes:
            lines.append(f"## {note.heading}")
            for b in note.bullet_points:
                lines.append(f"- {b}")
        return "\n".join(lines)[:2000]
