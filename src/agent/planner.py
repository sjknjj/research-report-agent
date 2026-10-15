"""规划智能体：把研究主题拆解为研究问题与报告提纲。"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from ..common.schema import AgentContext, ResearchPlan, SectionPlan
from ..llm.client import LLMError
from .base import BaseAgent

# 在线解析失败时的兜底提纲（与离线模板保持一致的研报方法论结构）
_FALLBACK_SECTIONS = [
    ("核心观点速览", "提炼研究对象的核心结论与关键假设", ["核心观点", "结论"]),
    ("行业背景与发展历程", "梳理行业定义、发展阶段与关键里程碑", ["行业 背景", "发展历程"]),
    ("市场规模与增长驱动", "分析市场容量、增速及其核心驱动因素", ["市场规模", "增长驱动"]),
    ("产业链与商业模式", "拆解上下游结构、价值分配与盈利模式", ["产业链", "商业模式"]),
    ("竞争格局与主要玩家", "对比主要参与者、市场份额与差异化定位", ["竞争格局", "主要企业"]),
    ("风险与不确定性", "识别政策、技术、需求、经营等层面的风险", ["风险", "不确定性"]),
    ("结论与展望", "给出综合判断、趋势展望与跟踪指标", ["结论", "未来展望"]),
]

_SYSTEM_PROMPT = (
    "你是一名资深行业研究主管，擅长把一个研究主题拆解为清晰、可执行的研究提纲。"
    "请严格输出 JSON（不要输出多余文字），结构为：\n"
    '{"objective": string, "questions": string[], '
    '"sections": [{"heading": string, "purpose": string, "keywords": string[]}], '
    '"keywords": string[]}\n'
    "要求：sections 控制在 5-8 个，覆盖背景、规模、产业链/商业模式、竞争格局、风险、结论；"
    "keywords 为可直接用于搜索引擎的中文检索词。"
)


class PlannerAgent(BaseAgent):
    role_name = "planner"

    def run(self, ctx: AgentContext) -> None:
        topic = ctx.topic
        self.logger.info("正在为主题「%s」生成研究提纲", topic)
        messages = [
            self._system(_SYSTEM_PROMPT),
            self._user(f"研究主题：{topic}。请输出研究提纲 JSON。"),
        ]
        plan: Optional[ResearchPlan] = None
        try:
            data = self.llm.chat_json(
                messages,
                stage="plan",
                context={"topic": topic},
                temperature=0.2,
            )
            plan = self._coerce(topic, data)
        except LLMError as exc:
            # 认证/配置类错误必须上抛，不能静默退化成内置模板
            self._raise_if_fatal(exc)
            self.logger.warning("提纲生成异常，使用内置方法论模板：%s", exc)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            self.logger.warning("提纲生成异常，使用内置方法论模板：%s", exc)

        if plan is None or not plan.sections:
            plan = self._fallback(topic)

        ctx.plan = plan
        ctx.log(f"[planner] 产出 {len(plan.sections)} 个章节、{len(plan.questions)} 个研究问题")
        self.logger.info("提纲完成：%s", " / ".join(s.heading for s in plan.sections))

    # -- 数据规整 -----------------------------------------------------------
    def _coerce(self, topic: str, data: Any) -> ResearchPlan:
        if not isinstance(data, dict):
            raise LLMError("提纲不是 JSON 对象")
        raw_sections = data.get("sections") or []
        sections: List[SectionPlan] = []
        for item in raw_sections:
            if isinstance(item, dict) and item.get("heading"):
                sections.append(
                    SectionPlan(
                        heading=str(item["heading"]),
                        purpose=str(item.get("purpose", "")),
                        keywords=[str(k) for k in item.get("keywords", []) if k],
                    )
                )
        if not sections:
            raise LLMError("提纲缺少 sections")
        return ResearchPlan(
            topic=topic,
            objective=str(data.get("objective", "")),
            questions=[str(q) for q in data.get("questions", []) if q],
            sections=sections,
            keywords=[str(k) for k in data.get("keywords", []) if k],
        )

    def _fallback(self, topic: str) -> ResearchPlan:
        sections = [
            SectionPlan(heading=h, purpose=p, keywords=kws)
            for (h, p, kws) in _FALLBACK_SECTIONS
        ]
        return ResearchPlan(
            topic=topic,
            objective=f"围绕“{topic}”形成结构完整的研究报告。",
            questions=[
                f"{topic}的发展阶段与核心驱动是什么？",
                f"{topic}的市场规模与竞争格局如何？",
                f"{topic}面临哪些风险，未来趋势如何？",
            ],
            sections=sections,
            keywords=[topic, f"{topic} 行业研究", f"{topic} 市场分析"],
        )
