"""审校智能体：结合机械校验与大模型评审，判断报告是否达标（3.0：新增引用覆盖率校验）。"""
from __future__ import annotations

import re
from typing import List, Tuple

from ..common.schema import AgentContext, ResearchReport, ReviewResult
from .base import BaseAgent

_SYSTEM_PROMPT = (
    "你是严格的研究报告质控编辑。请审查报告，输出 JSON："
    '{"passed": boolean, "score": number(0-100), "issues": string[], '
    '"suggestions": string[]}。重点检查：\n'
    "1. 结构完整性：有没有缺章节、缺摘要\n"
    "2. 逻辑自洽性：前后结论有没有自相矛盾？（比如前面说增长，后面说下滑）\n"
    "3. 数据支撑：每个判断/结论有没有对应的数字或事实支撑？不能空口说白话\n"
    "4. 引用匹配：带[n]标注的引用是否和正文对应？\n"
    "5. 空泛表述：有没有'可能上涨'、'值得关注'这种没有依据的废话？"
)

_MIN_ABSTRACT_LEN = 20
_MIN_SECTION_LEN = 30
# 3.0：引用覆盖率阈值——正文句子中带[n]引用标注的比例低于此值时报警
_MIN_CITATION_COVERAGE = 0.3
_CITATION_RE = re.compile(r"\[\d+\]")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？；\n]")


class ReviewerAgent(BaseAgent):
    role_name = "reviewer"

    def run(self, ctx: AgentContext, search_tool=None) -> None:
        report = ctx.report
        mechanical_issues, citation_stats = self._mechanical_check(report)
        llm_review = self._llm_review(ctx, report)

        issues = mechanical_issues + llm_review.issues
        passed = (not mechanical_issues) and llm_review.passed
        score = llm_review.score if not mechanical_issues else min(llm_review.score, 59)

        result = ReviewResult(
            passed=passed,
            score=score,
            issues=issues,
            suggestions=llm_review.suggestions,
        )
        ctx.review = result
        verdict = "通过" if result.passed else "需修订"
        self.logger.info(
            "审校结论：%s，评分 %s，问题 %d 条，引用覆盖率 %.0f%%",
            verdict, result.score, len(issues), citation_stats.get("coverage", 0) * 100,
        )
        ctx.log(f"[reviewer] {verdict}（score={result.score}，issues={len(issues)}，"
                f"引用覆盖率={citation_stats.get('coverage', 0):.0%}）")

    def _mechanical_check(self, report: ResearchReport) -> Tuple[List[str], dict]:
        issues: List[str] = []
        total_sentences = 0
        cited_sentences = 0

        if not report.abstract or len(report.abstract.strip()) < _MIN_ABSTRACT_LEN:
            issues.append("摘要缺失或过短。")
        if not report.sections:
            issues.append("报告没有任何正文章节。")
        ref_count = len(report.references)

        for section in report.sections:
            if not section.content or len(section.content.strip()) < _MIN_SECTION_LEN:
                issues.append(f"章节「{section.heading}」正文过短或缺失。")
            bad = [c for c in section.citations if c < 1 or c > ref_count]
            if bad:
                issues.append(f"章节「{section.heading}」存在越界引用编号：{bad}")

            # 3.0：统计该章节的引用覆盖率
            sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(section.content) if len(s.strip()) > 10]
            total_sentences += len(sentences)
            cited_sentences += sum(1 for s in sentences if _CITATION_RE.search(s))

        coverage = cited_sentences / total_sentences if total_sentences else 0.0
        stats = {"total_sentences": total_sentences, "cited_sentences": cited_sentences, "coverage": coverage}

        # 3.0：覆盖率过低时报警（不直接判不通过，作为建议）
        if total_sentences >= 5 and coverage < _MIN_CITATION_COVERAGE:
            issues.append(
                f"引用覆盖率偏低：{cited_sentences}/{total_sentences} 句有引用标注"
                f"（{coverage:.0%}），建议关键论断补充资料引用。"
            )

        return issues, stats

    def _llm_review(self, ctx: AgentContext, report: ResearchReport) -> ReviewResult:
        markdown = self._render_for_review(report)
        messages = [
            self._system(_SYSTEM_PROMPT),
            self._user(f"报告内容如下：\n\n{markdown}\n\n请输出审校 JSON。"),
        ]
        data = self._safe_chat_json(
            messages,
            fallback={"passed": True, "score": 80, "issues": [], "suggestions": []},
            stage="review",
            context={"section_count": len(report.sections)},
            temperature=0.1,
        )
        return ReviewResult(
            passed=bool(data.get("passed", True)),
            score=int(data.get("score", 80)),
            issues=[str(i) for i in data.get("issues", []) if i],
            suggestions=[str(s) for s in data.get("suggestions", []) if s],
        )

    @staticmethod
    def _render_for_review(report: ResearchReport) -> str:
        parts = [f"# {report.topic}\n\n## 摘要\n{report.abstract}\n"]
        for section in report.sections:
            parts.append(f"## {section.heading}\n{section.content}\n")
        return "\n".join(parts)[:6000]
