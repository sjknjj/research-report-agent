"""撰写智能体：把分析要点扩写为成稿，并组装完整研报（3.0：章节级并行撰写）。"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from ..common.schema import (
    AgentContext,
    AnalysisNote,
    Reference,
    ReportSection,
    ResearchReport,
)
from .base import BaseAgent

_SECTION_SYSTEM = (
    "你是资深研究报告撰稿人。请基于分析要点与资料，用专业、客观、连贯的中文书面语，"
    "把指定章节扩写为 2-4 段 Markdown 正文；关键论断后用 [编号] 标注引用资料；"
    "不要输出章节标题（标题会自动添加），不要使用“我认为”等主观口吻，不得编造资料中没有的数据。"
)
_ABSTRACT_SYSTEM = (
    "你是研究报告主编。请基于各章节要点，撰写 200-300 字的报告摘要，"
    "概括研究对象、核心判断、主要依据与风险提示，使用连贯的 Markdown 段落。"
)


class WriterAgent(BaseAgent):
    role_name = "writer"
    MAX_WORKERS = 4  # 3.0：并行撰写的章节数上限

    def run(self, ctx: AgentContext, search_tool=None) -> None:
        ctx.report = self._compose(ctx)
        ctx.log("[writer] 完成报告初稿撰写")

    def revise(self, ctx: AgentContext, suggestions: List[str]) -> None:
        """根据审校意见重新生成报告。"""
        extra = "；".join(suggestions) if suggestions else "提升论证密度与数据支撑"
        self.logger.info("根据审校意见修订：%s", extra)
        ctx.report = self._compose(ctx, extra_instruction=extra)
        ctx.log(f"[writer] 已按审校意见完成修订（{len(suggestions)} 条建议）")

    # ------------------------------------------------------------------
    def _compose(self, ctx: AgentContext, extra_instruction: str = "") -> ResearchReport:
        plan, notes = ctx.plan, ctx.notes
        headings = [n.heading for n in notes]

        abstract = self._write_abstract(ctx.topic, notes, extra_instruction)

        # 3.0：各章节正文并行撰写，按原始顺序组装
        sections: List[ReportSection] = [None] * len(notes)  # type: ignore
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            future_to_idx = {}
            for idx, note in enumerate(notes):
                fut = pool.submit(self._write_section, ctx.topic, note, extra_instruction)
                future_to_idx[fut] = (idx, note)

            for fut in as_completed(future_to_idx):
                idx, note = future_to_idx[fut]
                try:
                    content = fut.result()
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("章节「%s」撰写失败，使用要点兜底：%s", note.heading, exc)
                    content = "\n".join(f"- {b}" for b in note.bullet_points)
                sections[idx] = ReportSection(
                    heading=note.heading,
                    content=content,
                    citations=sorted(set(note.citation_indexes)),
                )

        # 3.0：在正文最前面插入「速览仪表盘」章节
        if ctx.debate is not None:
            sections.insert(0, self._render_dashboard(ctx.debate))
            # 3.0：在正文最后追加「多空对照」章节（来自辩论结果）
            sections.append(self._render_debate_section(ctx.debate))

        references = [
            Reference(index=i, title=r.title, url=r.url, source=r.source)
            for i, r in enumerate(ctx.search_results, start=1)
        ]

        return ResearchReport(
            topic=plan.topic,
            abstract=abstract,
            sections=sections,
            references=references,
            language=self.settings.language,
            mode=self.settings.effective_mode,
            meta={
                "objective": plan.objective,
                "questions": plan.questions,
                "model": self.settings.llm_model,
                "source_count": len(ctx.search_results),
                "revised": bool(extra_instruction),
                "debate_score": ctx.debate.score if ctx.debate else None,
            },
        )

    @staticmethod
    def _render_debate_section(debate) -> ReportSection:
        """3.0：把多空辩论结果渲染成一个对照章节。"""
        lines = [
            "| 维度 | 乐观观点（多方） | 谨慎观点（空方） |",
            "| --- | --- | --- |",
        ]
        max_len = max(len(debate.bull_points), len(debate.bear_points))
        for i in range(max_len):
            bull = debate.bull_points[i] if i < len(debate.bull_points) else "—"
            bear = debate.bear_points[i] if i < len(debate.bear_points) else "—"
            lines.append(f"| 论点 {i+1} | {bull} | {bear} |")

        content = "\n".join(lines) + "\n\n"
        if debate.rebuttal:
            content += f"**多方反驳**：{debate.rebuttal}\n\n"
        if debate.synthesis:
            content += f"**综合判断**：{debate.synthesis}\n\n"
        content += f"**多空评分**：{debate.score}/10（5为中性）"

        return ReportSection(
            heading="多空对照与综合判断",
            content=content,
            citations=[],
        )

    @staticmethod
    def _render_dashboard(debate) -> ReportSection:
        """3.0：生成报告开头的「速览仪表盘」——一眼能看到核心结论。"""
        score = debate.score
        # 评分对应的文字标签
        if score >= 8:
            verdict = "强烈偏多"
        elif score >= 6:
            verdict = "偏多"
        elif score >= 4:
            verdict = "中性"
        elif score >= 2:
            verdict = "偏空"
        else:
            verdict = "强烈偏空"

        bull_top = debate.bull_points[:3] or ["—"]
        bear_top = debate.bear_points[:3] or ["—"]

        lines = [
            f"| 项目 | 内容 |",
            f"| --- | --- |",
            f"| **多空评分** | **{score}/10**（{verdict}） |",
            f"| **综合判断** | {debate.synthesis or '详见下文分析'} |",
        ]
        content = "\n".join(lines) + "\n\n"
        content += "**核心驱动（多方）**：\n"
        for p in bull_top:
            content += f"- {p}\n"
        content += "\n**主要风险（空方）**：\n"
        for p in bear_top:
            content += f"- {p}\n"

        return ReportSection(
            heading="速览仪表盘",
            content=content,
            citations=[],
        )

    def _write_abstract(
        self, topic: str, notes: List[AnalysisNote], extra: str
    ) -> str:
        digest_lines = []
        for note in notes:
            digest_lines.append(f"- {note.heading}：" + "；".join(note.bullet_points[:2]))
        digest = "\n".join(digest_lines)
        instruction = f"\n额外要求：{extra}" if extra else ""
        messages = [
            self._system(_ABSTRACT_SYSTEM),
            self._user(
                f"研究主题：{topic}\n各章节要点：\n{digest}{instruction}\n请撰写报告摘要。"
            ),
        ]
        text = self._safe_chat(
            messages,
            fallback=f"（围绕「{topic}」的摘要占位，在线模式将自动生成。）",
            stage="abstract",
            context={"topic": topic, "headings": [n.heading for n in notes]},
            temperature=0.4,
        )
        return self._clean(text)

    def _write_section(
        self, topic: str, note: AnalysisNote, extra: str
    ) -> str:
        bullets = "\n".join(f"- {b}" for b in note.bullet_points)
        cite_hint = (
            f"可引用资料编号：{note.citation_indexes}" if note.citation_indexes else ""
        )
        instruction = f"\n修订要求：{extra}" if extra else ""
        messages = [
            self._system(_SECTION_SYSTEM),
            self._user(
                f"研究主题：{topic}\n章节：{note.heading}\n{cite_hint}\n"
                f"分析要点：\n{bullets}{instruction}\n请撰写该章节正文。"
            ),
        ]
        text = self._safe_chat(
            messages,
            fallback=bullets,
            stage="write_section",
            context={"heading": note.heading, "bullets": note.bullet_points},
            temperature=0.5,
        )
        return self._clean(text)

    @staticmethod
    def _clean(text: str) -> str:
        text = text.strip()
        # 去掉模型可能重复输出的首个同级标题行，避免与自动标题重复
        text = re.sub(r"^\s{0,3}#{1,6}\s+.*(\n|$)", "", text, count=1).strip()
        return text
