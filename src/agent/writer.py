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
from ..llm.client import LLMError
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
            },
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
        try:
            text = self.llm.chat(
                messages,
                stage="abstract",
                context={"topic": topic, "headings": [n.heading for n in notes]},
                temperature=0.4,
            )
        except LLMError as exc:
            self.logger.warning("摘要生成失败，使用占位：%s", exc)
            text = f"（围绕「{topic}」的摘要占位，在线模式将自动生成。）"
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
        try:
            text = self.llm.chat(
                messages,
                stage="write_section",
                context={"heading": note.heading, "bullets": note.bullet_points},
                temperature=0.5,
            )
        except LLMError as exc:
            self.logger.warning("章节「%s」生成失败，使用要点兜底：%s", note.heading, exc)
            text = bullets
        return self._clean(text)

    @staticmethod
    def _clean(text: str) -> str:
        text = text.strip()
        # 去掉模型可能重复输出的首个同级标题行，避免与自动标题重复
        text = re.sub(r"^\s{0,3}#{1,6}\s+.*(\n|$)", "", text, count=1).strip()
        return text
