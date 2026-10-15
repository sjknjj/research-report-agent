"""分析智能体：逐章节基于检索证据提炼分析要点（3.0：章节级并行）。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from ..common.schema import AgentContext, AnalysisNote, SearchResult, SectionPlan
from .base import BaseAgent

_SYSTEM_PROMPT = (
    "你是一名严谨的行业分析师。请基于给定的检索资料，为指定报告章节提炼 3-6 条分析要点，"
    "每条要点须有资料支撑，避免无依据的臆断。严格输出 JSON："
    '{"bullet_points": string[], "citation_indexes": number[]}，'
    "其中 citation_indexes 为你实际依据的资料编号（整数）。"
)


class AnalystAgent(BaseAgent):
    role_name = "analyst"
    TOP_K_EVIDENCE = 5
    MAX_WORKERS = 4  # 3.0：并行分析的章节数上限（IO密集型，线程池即可）

    def run(self, ctx: AgentContext, search_tool=None) -> None:
        if ctx.plan is None:
            raise RuntimeError("分析阶段缺少研究提纲")
        results = ctx.search_results
        sections = ctx.plan.sections

        # 预计算每章的证据编号（纯CPU操作，无需并行）
        plan_pairs = []
        for section in sections:
            evidence_idx = self._rank_evidence(section, results)
            plan_pairs.append((section, evidence_idx))

        # 3.0：章节分析并行执行，按原始顺序回填
        notes: List[AnalysisNote] = [None] * len(sections)  # type: ignore
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            future_to_idx = {}
            for idx, (section, evidence_idx) in enumerate(plan_pairs):
                fut = pool.submit(self._analyze_section, ctx.topic, section, results, evidence_idx)
                future_to_idx[fut] = idx

            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                section = sections[idx]
                try:
                    note = fut.result()
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("章节「%s」分析失败，使用兜底：%s", section.heading, exc)
                    note = AnalysisNote(
                        heading=section.heading,
                        bullet_points=[f"{section.heading}：分析失败，需人工补充。"],
                        citation_indexes=[],
                    )
                notes[idx] = note
                self.logger.info(
                    "章节「%s」提炼 %d 条要点，引用资料 %s",
                    section.heading,
                    len(note.bullet_points),
                    note.citation_indexes or "无",
                )

        ctx.notes = notes
        ctx.log(f"[analyst] 完成 {len(notes)} 个章节的要点分析（并行{self.MAX_WORKERS}线程）")

    def _analyze_section(
        self,
        topic: str,
        section: SectionPlan,
        results: List[SearchResult],
        evidence_idx: List[int],
    ) -> AnalysisNote:
        evidence_block = self._format_evidence(results, evidence_idx)
        messages = [
            self._system(_SYSTEM_PROMPT),
            self._user(
                f"研究主题：{topic}\n当前章节：{section.heading}\n"
                f"章节目标：{section.purpose}\n\n检索资料：\n{evidence_block}\n\n"
                "请输出该章节的分析要点 JSON。"
            ),
        ]
        offline_evidence = [self._result_brief(results[i - 1]) for i in evidence_idx]
        data = self._safe_chat_json(
            messages,
            fallback={
                "bullet_points": [
                    f"{section.heading}：需结合资料界定口径并给出量化与定性判断。",
                    "关键论断应标注资料编号，确保可追溯。",
                ],
                "citation_indexes": evidence_idx[:2],
            },
            stage="section_analysis",
            context={
                "heading": section.heading,
                "evidence": offline_evidence,
                "citation_indexes": evidence_idx,
            },
            label=section.heading,
        )
        bullets = [str(b) for b in data.get("bullet_points", []) if b]
        cites = [int(c) for c in data.get("citation_indexes", []) if str(c).isdigit()]
        cites = [c for c in cites if c in evidence_idx]

        if not bullets:
            bullets = [f"{section.heading}：现有资料有限，建议补充权威来源后再下结论。"]
        return AnalysisNote(
            heading=section.heading, bullet_points=bullets, citation_indexes=cites
        )

    def _rank_evidence(self, section: SectionPlan, results: List[SearchResult]) -> List[int]:
        """按关键词命中度为章节挑选最相关的资料编号（1 起）。"""
        keys = [section.heading] + section.keywords
        scored = []
        for idx, result in enumerate(results, start=1):
            text = (result.title + " " + result.snippet + " " + result.fetched_content).lower()
            score = sum(1 for k in keys if k and k.lower() in text)
            scored.append((score, idx))
        scored.sort(key=lambda x: (-x[0], x[1]))
        chosen = [idx for score, idx in scored[: self.TOP_K_EVIDENCE] if score > 0]
        if not chosen:
            # 没有命中时退化为取全局前若干条，保证分析有输入
            chosen = [idx for _, idx in scored[: self.TOP_K_EVIDENCE]]
        return sorted(chosen)

    @staticmethod
    def _result_brief(result: SearchResult) -> str:
        text = result.fetched_content or result.snippet or result.title
        return text[:200]

    def _format_evidence(self, results: List[SearchResult], indexes: List[int]) -> str:
        blocks = []
        for idx in indexes:
            r = results[idx - 1]
            brief = (r.fetched_content or r.snippet or r.title)[:400]
            blocks.append(f"[{idx}] {r.title}\n{brief}")
        return "\n\n".join(blocks) if blocks else "（暂无检索资料）"
