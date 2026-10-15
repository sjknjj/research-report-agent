"""多空辩论智能体（3.0新增）：Bull vs Bear 对抗分析，避免单边视角。

流程：
1. BullAgent 从乐观视角提炼 3-5 个核心论据（增长、催化、估值优势）
2. BearAgent 从谨慎视角提炼 3-5 个核心论据（风险、不确定性、估值压力）
3. 第 1 轮反驳：双方各看对方论据做一轮反驳（多方反驳空方、空方反驳多方）
4. 第 2 轮反驳（A3）：多方针对空方的第 1 轮反驳再反驳、空方针对多方的第 1 轮反驳再反驳；
   轮数由 ``settings.debate_rounds`` 控制（默认 2，可配成 1 只跑第 1 轮）
5. 综合成平衡观点，给出 0-10 的多空评分

注意：多空双方的论点与反驳必须合并进同一个
:class:`~src.common.schema.DebateResult`（使用 schema 声明的
``bull_rebuttal`` / ``bear_rebuttal`` 字段），否则空方内容会被整块丢弃。
"""
from __future__ import annotations

from typing import List

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

_REBUTTAL_SYSTEM = (
    "你是辩论选手。以下是对方的核心论点，请针对其中最强的 2-3 点做简短反驳"
    "（每点一两句），指出其论证漏洞或被忽略的反证。严格输出 JSON："
    '{"rebuttal": string}。'
)

# A3：第 2 轮反驳——针对对方上一轮的反驳再反驳，而不是重复第 1 轮。
_REBUTTAL_ROUND2_SYSTEM = (
    "你是辩论选手。以下是你方在第 1 轮中被对方反驳的观点，以及对方的第 1 轮反驳。"
    "请针对对方反驳中的漏洞、偷换概念或缺失的反证，做第 2 轮再反驳"
    "（每点一两句）。严格输出 JSON："
    '{"rebuttal": string}。'
)

_SYNTHESIS_SYSTEM = (
    "你是研究主管。以下是乐观派和谨慎派的论点与互相反驳，请做一段 150-200 字的"
    "综合平衡判断，明确当前多空力量对比、关键观察变量。直接输出文字，不要JSON。"
)


class DebateAgent(BaseAgent):
    """多空辩论智能体。"""

    role_name = "debate"

    def run(self, ctx: AgentContext) -> None:
        if not ctx.notes:
            raise RuntimeError("辩论阶段缺少分析要点")

        digest = self._format_notes(ctx)
        self.logger.info("开始两方辩论（多空），基于 %d 个章节的分析要点", len(ctx.notes))

        bull = self._bull_view(ctx.topic, digest)
        bear = self._bear_view(ctx.topic, digest)
        self.logger.info("多方 %d 个论点，空方 %d 个论点",
                         len(bull.bull_points), len(bear.bear_points))

        # 第 1 轮：多方反驳空方 → opponent_role 是对方（谨慎派）；
        # 空方反驳多方 → opponent_role 是对方（乐观派）。角色不能传反。
        bull_rebuttal = self._rebuttal(ctx.topic, "谨慎派", bear.bear_points)
        bear_rebuttal = self._rebuttal(ctx.topic, "乐观派", bull.bull_points)
        self.logger.info("第 1 轮反驳完成")

        # 第 2 轮（A3）：多方再反驳空方的第 1 轮反驳，空方再反驳多方的第 1 轮反驳。
        rounds = self._resolve_rounds()
        bull_rebuttal_round2 = ""
        bear_rebuttal_round2 = ""
        if rounds >= 2:
            bull_rebuttal_round2 = self._rebuttal_round2(ctx.topic, "谨慎派", bear_rebuttal)
            bear_rebuttal_round2 = self._rebuttal_round2(ctx.topic, "乐观派", bull_rebuttal)
            self.logger.info("第 2 轮反驳完成")

        # 关键：把多空合并进同一个 DebateResult，空方论点/反驳不得丢弃。
        debate = DebateResult(
            bull_points=bull.bull_points,
            bear_points=bear.bear_points,
            bull_rebuttal=bull_rebuttal,
            bear_rebuttal=bear_rebuttal,
            bull_rebuttal_round2=bull_rebuttal_round2,
            bear_rebuttal_round2=bear_rebuttal_round2,
            debate_rounds=rounds,
            score=self._final_score(bull.score, bear.score),
        )
        debate.synthesis = self._synthesis(ctx.topic, debate)
        self.logger.info("辩论综合评分：%d/10（共 %d 轮反驳）", debate.score, rounds)

        ctx.debate = debate
        ctx.log(f"[debate] 两方辩论完成（{rounds} 轮反驳，评分 {debate.score}/10，"
                f"多方 {len(debate.bull_points)} 条 / 空方 {len(debate.bear_points)} 条）")

    # ------------------------------------------------------------------
    def _resolve_rounds(self) -> int:
        """反驳轮数：读取 ``settings.debate_rounds``，本实现支持 1 或 2 轮。"""
        raw = getattr(self.settings, "debate_rounds", 2)
        rounds = self._to_int(raw, 2)
        if rounds < 1:
            return 1
        if rounds > 2:
            # 目前只实现两轮；配成更多轮时明确告知，避免"静默少做"。
            self.logger.warning("debate_rounds=%s 超出实现上限，按 2 轮执行", rounds)
            return 2
        return rounds

    # ------------------------------------------------------------------
    def _bull_view(self, topic: str, digest: str) -> DebateResult:
        data = self._safe_chat_json(
            [self._system(_BULL_SYSTEM),
             self._user(f"研究主题：{topic}\n分析要点：\n{digest}")],
            fallback={"points": ["离线模式：乐观观点占位"], "score": 6},
            stage="bull_view", temperature=0.3,
        )
        return DebateResult(
            bull_points=self._to_str_list(data.get("points")),
            score=self._to_int(data.get("score"), 7),
        )

    def _bear_view(self, topic: str, digest: str) -> DebateResult:
        data = self._safe_chat_json(
            [self._system(_BEAR_SYSTEM),
             self._user(f"研究主题：{topic}\n分析要点：\n{digest}")],
            fallback={"points": ["离线模式：风险观点占位"], "score": 4},
            stage="bear_view", temperature=0.3,
        )
        return DebateResult(
            bear_points=self._to_str_list(data.get("points")),
            score=self._to_int(data.get("score"), 4),
        )

    def _rebuttal(self, topic: str, opponent_role: str, opponent_points: List[str]) -> str:
        """第 1 轮：针对对方论点生成反驳；opponent_role 是对方的角色名。"""
        if not opponent_points:
            return ""
        points_text = "\n".join(f"- {p}" for p in opponent_points)
        return self._ask_rebuttal(
            topic, opponent_role, points_text, _REBUTTAL_SYSTEM, stage="rebuttal"
        )

    def _rebuttal_round2(self, topic: str, opponent_role: str, opponent_rebuttal: str) -> str:
        """第 2 轮：针对对方第 1 轮反驳再反驳；对方没有反驳时跳过。"""
        text = (opponent_rebuttal or "").strip()
        if not text:
            return ""
        return self._ask_rebuttal(
            topic, opponent_role, text, _REBUTTAL_ROUND2_SYSTEM, stage="rebuttal_round2"
        )

    def _ask_rebuttal(
        self,
        topic: str,
        opponent_role: str,
        opponent_text: str,
        system_prompt: str,
        stage: str,
    ) -> str:
        """调用 LLM 生成一段反驳；非法 JSON / 瞬时失败时退回占位文本，不阻断流水线。"""
        data = self._safe_chat_json(
            [self._system(system_prompt),
             self._user(f"研究主题：{topic}\n对方（{opponent_role}）的论述：\n{opponent_text}")],
            fallback={"rebuttal": "（离线模式：反驳占位）"},
            stage=stage, temperature=0.2,
        )
        return str(data.get("rebuttal", "") or "")

    def _synthesis(self, topic: str, debate: DebateResult) -> str:
        prompt = (
            f"研究主题：{topic}\n\n"
            f"乐观派论点：\n" + "\n".join(f"- {p}" for p in debate.bull_points) +
            f"\n乐观派反驳空方（第1轮）：{debate.bull_rebuttal}\n\n"
            f"谨慎派论点：\n" + "\n".join(f"- {p}" for p in debate.bear_points) +
            f"\n谨慎派反驳多方（第1轮）：{debate.bear_rebuttal}\n"
        )
        if debate.bull_rebuttal_round2 or debate.bear_rebuttal_round2:
            prompt += (
                f"\n乐观派再反驳（第2轮）：{debate.bull_rebuttal_round2}\n"
                f"谨慎派再反驳（第2轮）：{debate.bear_rebuttal_round2}\n"
            )
        prompt += "\n请综合平衡判断。"
        return self._safe_chat(
            [self._system(_SYNTHESIS_SYSTEM), self._user(prompt)],
            fallback="（离线模式：综合观点占位）",
            stage="debate_synthesis", temperature=0.4,
        )

    @staticmethod
    def _final_score(bull_score: int, bear_score: int) -> int:
        # bull_score 是多方自评分（0-10），bear_score 是空方自评分（0-10）
        # 转换成统一的多空评分：越高越偏多
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
