"""多空辩论智能体（3.0新增）：Bull vs Bear 对抗分析，避免单边视角。

流程：
1. BullAgent 从乐观视角提炼 3-5 个核心论据（增长、催化、估值优势）
2. BearAgent 从谨慎视角提炼 3-5 个核心论据（风险、不确定性、估值压力）
3. 双方各看对方论据做一轮反驳
4. 综合成平衡观点，给出 0-10 的多空评分

3.4 升级：支持三方辩论模式（技术面 vs 基本面 vs 情绪面）
"""
from __future__ import annotations

from typing import List

from ..common.logger import get_logger
from ..common.schema import AgentContext, DebateResult
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

# 3.4：三方辩论视角
_TECHNICAL_SYSTEM = (
    "你是技术面分析师。从已有的行情/技术指标数据中，提炼 3-5 个关键技术信号"
    "（趋势/支撑阻力/量能/形态），基于已有数据，不要编造。严格输出 JSON："
    '{"points": string[], "score": number(0-10)}。'
)

_FUNDAMENTAL_SYSTEM = (
    "你是基本面分析师。从已有的财务/估值/行业数据中，提炼 3-5 个核心基本面判断"
    "（盈利增长/估值水平/行业地位），基于已有数据，不要编造。严格输出 JSON："
    '{"points": string[], "score": number(0-10)}。'
)

_SENTIMENT_SYSTEM = (
    "你是情绪面分析师。从已有的资讯/舆情/资金流向数据中，提炼 3-5 个市场情绪信号"
    "（利好/利空/资金动向/舆论热度），基于已有数据，不要编造。严格输出 JSON："
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

_SYNTHESIS_3WAY_SYSTEM = (
    "你是研究主管。以下是技术面、基本面、情绪面三方的分析观点，请做一段 200-300 字的"
    "综合平衡判断，明确三方分歧在哪、共识在哪、当前最关键的观察变量是什么。"
    "直接输出文字，不要JSON。"
)


class DebateAgent(BaseAgent):
    """多空辩论智能体。"""

    role_name = "debate"

    def run(self, ctx: AgentContext, search_tool=None) -> None:
        if not ctx.notes:
            raise RuntimeError("辩论阶段缺少分析要点")

        digest = self._format_notes(ctx)
        debate_mode = getattr(self.settings, "debate_mode", "two_way")

        if debate_mode == "three_way":
            self._run_three_way(ctx, digest)
        else:
            self._run_two_way(ctx, digest)

    def _run_two_way(self, ctx: AgentContext, digest: str) -> None:
        """传统两方辩论（多空）。"""
        self.logger.info("开始两方辩论（多空），基于 %d 个章节的分析要点", len(ctx.notes))

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
        ctx.log(f"[debate] 两方辩论完成（评分 {bull.score}/10）")

    def _run_three_way(self, ctx: AgentContext, digest: str) -> None:
        """3.4 新增：三方辩论（技术面 vs 基本面 vs 情绪面）。"""
        self.logger.info("开始三方辩论（技术/基本面/情绪），基于 %d 个章节的分析要点", len(ctx.notes))

        technical = self._safe_chat_json(
            [self._system(_TECHNICAL_SYSTEM),
             self._user(f"研究主题：{ctx.topic}\n分析要点：\n{digest}")],
            fallback={"points": ["离线模式：技术面观点占位"], "score": 5},
            stage="technical_view", temperature=0.3,
        )
        fundamental = self._safe_chat_json(
            [self._system(_FUNDAMENTAL_SYSTEM),
             self._user(f"研究主题：{ctx.topic}\n分析要点：\n{digest}")],
            fallback={"points": ["离线模式：基本面观点占位"], "score": 5},
            stage="fundamental_view", temperature=0.3,
        )
        sentiment = self._safe_chat_json(
            [self._system(_SENTIMENT_SYSTEM),
             self._user(f"研究主题：{ctx.topic}\n分析要点：\n{digest}")],
            fallback={"points": ["离线模式：情绪面观点占位"], "score": 5},
            stage="sentiment_view", temperature=0.3,
        )

        tech_score = int(technical.get("score", 5))
        fund_score = int(fundamental.get("score", 5))
        sent_score = int(sentiment.get("score", 5))
        avg_score = round((tech_score + fund_score + sent_score) / 3)

        synthesis_input = (
            f"技术面观点：\n" + "\n".join(f"- {p}" for p in technical.get("points", [])) +
            f"\n技术面评分：{tech_score}/10\n\n"
            f"基本面观点：\n" + "\n".join(f"- {p}" for p in fundamental.get("points", [])) +
            f"\n基本面评分：{fund_score}/10\n\n"
            f"情绪面观点：\n" + "\n".join(f"- {p}" for p in sentiment.get("points", [])) +
            f"\n情绪面评分：{sent_score}/10\n\n"
        )
        synthesis = self._safe_chat(
            [self._system(_SYNTHESIS_3WAY_SYSTEM), self._user(synthesis_input)],
            fallback="（离线模式：综合观点占位）",
            stage="synthesis_3way", temperature=0.4,
        )

        result = DebateResult(
            bull_points=technical.get("points", []),
            bear_points=fundamental.get("points", []),
            synthesis=synthesis,
            score=avg_score,
        )
        # 把情绪面观点存在 rebuttal 字段里复用
        result.rebuttal = "情绪面：" + "；".join(sentiment.get("points", [])[:3])

        ctx.debate = result
        ctx.log(f"[debate] 三方辩论完成（综合评分 {avg_score}/10）")

    # ------------------------------------------------------------------
    def _bull_view(self, topic: str, digest: str) -> DebateResult:
        data = self._safe_chat_json(
            [self._system(_BULL_SYSTEM),
             self._user(f"研究主题：{topic}\n分析要点：\n{digest}")],
            fallback={"points": ["离线模式：乐观观点占位"], "score": 6},
            stage="bull_view", temperature=0.3,
        )
        return DebateResult(
            bull_points=[str(p) for p in data.get("points", []) if p],
            score=int(data.get("score", 7)),
        )

    def _bear_view(self, topic: str, digest: str) -> DebateResult:
        data = self._safe_chat_json(
            [self._system(_BEAR_SYSTEM),
             self._user(f"研究主题：{topic}\n分析要点：\n{digest}")],
            fallback={"points": ["离线模式：风险观点占位"], "score": 4},
            stage="bear_view", temperature=0.3,
        )
        return DebateResult(
            bear_points=[str(p) for p in data.get("points", []) if p],
            score=int(data.get("score", 4)),
        )

    def _rebuttal(self, topic: str, opponent_role: str, opponent_points: List[str]) -> str:
        if not opponent_points:
            return ""
        points_text = "\n".join(f"- {p}" for p in opponent_points)
        data = self._safe_chat_json(
            [self._system(_REBUTTAL_SYSTEM),
             self._user(f"对方（{opponent_role}）论点：\n{points_text}")],
            fallback={"rebuttal": "（离线模式：反驳占位）"},
            stage="rebuttal", temperature=0.2,
        )
        return str(data.get("rebuttal", ""))

    def _synthesis(self, topic: str, bull: DebateResult, bear: DebateResult) -> str:
        prompt = (
            f"研究主题：{topic}\n\n"
            f"乐观派论点：\n" + "\n".join(f"- {p}" for p in bull.bull_points) +
            f"\n乐观派反驳空方：{bull.rebuttal}\n\n"
            f"谨慎派论点：\n" + "\n".join(f"- {p}" for p in bear.bear_points) +
            f"\n谨慎派反驳多方：{bear.rebuttal}\n\n"
            "请综合平衡判断。"
        )
        return self._safe_chat(
            [self._system(_SYNTHESIS_SYSTEM), self._user(prompt)],
            fallback="（离线模式：综合观点占位）",
            stage="debate_synthesis", temperature=0.4,
        )

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
