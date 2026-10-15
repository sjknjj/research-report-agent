"""审校智能体：结合机械校验与大模型评审，判断报告是否达标。

第 ⑥ 步实际执行的检查：
1. 结构完整性：摘要/章节是否齐全、正文是否过短；
2. 引用编号区间：章节内的 ``[n]`` 是否越界；
3. 引用覆盖率：正文章节中带 ``[n]`` 标注的句子占比（**非阻断建议**，
   且自动生成章节不参与统计）；
4. 大模型逻辑复核：逻辑自洽、数据支撑、引用匹配、空泛表述。

注意：本路径为行业/主题研报，**没有标的公司的 FactBase**，因此不包含
逐条数字校验（CrossVerifier）。数字校验只在投研助手（公司速览）路径执行；
报告结尾的「反幻觉总览」会如实标注该项为“未启用”。
"""
from __future__ import annotations

import re
from typing import List, Tuple

from ..common.schema import (
    AUTO_SECTION_HEADINGS,
    AgentContext,
    ResearchReport,
    ReviewResult,
)
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
# 3.0：引用覆盖率阈值——正文句子中带[n]引用标注的比例低于此值时给出“建议”
# （历史实现把它当成阻断问题，而自动生成章节 citations 恒为空，
#  导致审校永远无法通过、白白烧修订轮数，故改为非阻断建议）
_MIN_CITATION_COVERAGE = 0.3
_CITATION_RE = re.compile(r"\[\d+\]")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？；\n]")


class ReviewerAgent(BaseAgent):
    role_name = "reviewer"

    def run(self, ctx: AgentContext) -> None:
        report = ctx.report
        mechanical_issues, coverage_stats = self._mechanical_check(report)
        llm_review = self._llm_review(ctx, report)

        issues = mechanical_issues + llm_review.issues
        passed = (not mechanical_issues) and llm_review.passed
        score = llm_review.score if not mechanical_issues else min(llm_review.score, 59)

        # 引用覆盖率是“非阻断建议”，不参与 passed 判定，避免永远无法收敛
        suggestions = list(llm_review.suggestions)
        coverage = coverage_stats.get("coverage", 0.0)
        if coverage_stats.get("total_sentences", 0) >= 5 and coverage < _MIN_CITATION_COVERAGE:
            suggestions.append(
                f"引用覆盖率偏低：{coverage_stats['cited_sentences']}/"
                f"{coverage_stats['total_sentences']} 句有引用标注（{coverage:.0%}），"
                "建议关键论断补充资料引用（非阻断项）。"
            )

        result = ReviewResult(
            passed=passed,
            score=score,
            issues=issues,
            suggestions=suggestions,
        )
        ctx.review = result
        self._record_checks(ctx, result, mechanical_issues, coverage_stats, llm_review)

        verdict = "通过" if result.passed else "需修订"
        self.logger.info(
            "审校结论：%s，评分 %s，问题 %d 条，引用覆盖率 %.0f%%（非阻断）",
            verdict, result.score, len(issues), coverage * 100,
        )
        ctx.log(f"[reviewer] {verdict}（score={result.score}，issues={len(issues)}，"
                f"引用覆盖率={coverage:.0%}，非阻断）")

    def _record_checks(
        self,
        ctx: AgentContext,
        result: ReviewResult,
        mechanical_issues: List[str],
        coverage_stats: dict,
        llm_review: ReviewResult,
    ) -> None:
        """把本轮真实执行过的校验登记到 ctx，供「反幻觉总览」如实展示。"""
        ctx.mark_check(
            "structure",
            "结构完整性校验（摘要/章节齐全、正文长度）",
            executed=True,
            passed=not mechanical_issues,
            detail=f"问题 {len(mechanical_issues)} 条",
        )
        ctx.mark_check(
            "citation_range",
            "引用编号区间校验",
            executed=True,
            passed=not any("越界引用" in i for i in mechanical_issues),
            detail=f"参考文献 {len(ctx.report.references) if ctx.report else 0} 条",
        )
        ctx.mark_check(
            "citation_coverage",
            "引用覆盖率检查（非阻断建议）",
            executed=True,
            passed=None,
            detail=(f"{coverage_stats.get('cited_sentences', 0)}/"
                    f"{coverage_stats.get('total_sentences', 0)} 句有引用"
                    f"（{coverage_stats.get('coverage', 0.0):.0%}），已排除 "
                    f"{coverage_stats.get('excluded_sections', 0)} 个自动生成章节"),
        )
        ctx.mark_check(
            "llm_review",
            "大模型逻辑审校（自洽/支撑/引用/空泛）",
            executed=True,
            passed=llm_review.passed,
            detail=f"评分 {llm_review.score}，问题 {len(llm_review.issues)} 条",
        )

    def _mechanical_check(self, report: ResearchReport) -> Tuple[List[str], dict]:
        issues: List[str] = []
        total_sentences = 0
        cited_sentences = 0
        excluded_sections = 0

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

            # 3.0：统计引用覆盖率；自动生成章节不参与（它们没有检索证据可引）
            if section.heading in AUTO_SECTION_HEADINGS:
                excluded_sections += 1
                continue
            sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(section.content) if len(s.strip()) > 10]
            total_sentences += len(sentences)
            cited_sentences += sum(1 for s in sentences if _CITATION_RE.search(s))

        coverage = cited_sentences / total_sentences if total_sentences else 0.0
        stats = {
            "total_sentences": total_sentences,
            "cited_sentences": cited_sentences,
            "coverage": coverage,
            "excluded_sections": excluded_sections,
        }
        return issues, stats

    def _llm_review(self, ctx: AgentContext, report: ResearchReport) -> ReviewResult:
        markdown = self._render_for_review(report)
        # U5：把引用溯源校验中“疑似对不上”的数字作为提示补充给审校模型（非阻断，
        # 只供 LLM 参考；即使为空也不影响审校结论）。
        hint = self._citation_hint(report)
        messages = [
            self._system(_SYSTEM_PROMPT),
            self._user(f"报告内容如下：\n\n{markdown}\n\n{hint}请输出审校 JSON。"),
        ]
        data = self._safe_chat_json(
            messages,
            fallback={"passed": True, "score": 80, "issues": [], "suggestions": []},
            stage="review",
            context={"section_count": len(report.sections)},
            temperature=0.1,
        )
        return ReviewResult(
            # 注意：bool("false") == True，必须用 _to_bool 做健壮解析，
            # 否则模型返回字符串 "false" 时会把“审校失败”误判为“通过”。
            passed=self._to_bool(data.get("passed"), True),
            score=self._to_int(data.get("score"), 80),
            issues=self._to_str_list(data.get("issues")),
            suggestions=self._to_str_list(data.get("suggestions")),
        )

    @staticmethod
    def _citation_hint(report: ResearchReport) -> str:
        """U5：把引用溯源校验中疑似对不上的数字整理成审校提示（非阻断）。"""
        info = (report.meta or {}).get("citation") or {}
        ungrounded = info.get("ungrounded") or []
        if not info.get("checked") or not ungrounded:
            return ""
        lines = [
            "【引用溯源提示（自动校验，非阻断）】"
            "以下数字未能在其标注的引用资料中找到，请重点核对是否编造或引用错位："
        ]
        for item in ungrounded[:10]:
            lines.append(
                f"- 数字 {item.get('number')}（引用 {item.get('citation')}）："
                f"{item.get('snippet', '')}"
            )
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def _render_for_review(report: ResearchReport) -> str:
        parts = [f"# {report.topic}\n\n## 摘要\n{report.abstract}\n"]
        for section in report.sections:
            parts.append(f"## {section.heading}\n{section.content}\n")
        return "\n".join(parts)[:6000]
