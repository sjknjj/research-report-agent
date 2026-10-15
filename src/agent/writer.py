"""撰写智能体：把分析要点扩写为成稿，并组装完整研报（3.0：章节级并行撰写）。"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from ..common.schema import (
    AUTO_SECTION_HEADINGS,
    AgentContext,
    AnalysisNote,
    Reference,
    ReportSection,
    ResearchReport,
)
from ..verify.citation import check_citations
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

    def run(self, ctx: AgentContext) -> None:
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
                    # 认证/配置类错误不能被章节级兜底吞掉
                    self._raise_if_fatal(exc)
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

        # U5：先跑「引用溯源校验」并登记到 ctx，再渲染反幻觉总览，
        # 保证总览里这条检查的状态来自本轮真实执行结果。
        citation = self._run_citation_check(ctx, sections)

        # 3.5：在报告最后加「反幻觉总览」章节（由真实执行过的校验驱动）
        sections.append(self.render_anti_hallucination(ctx))

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
                "citation": citation,
                "checks": {k: v.to_dict() for k, v in ctx.checks.items()},
            },
        )

    # -- U5：引用溯源校验 ----------------------------------------------------
    @staticmethod
    def _body_text(sections: List[ReportSection]) -> str:
        """只取「正文章节」文本，排除速览仪表盘/多空对照/反幻觉总览等自动生成章节。"""
        return "\n".join(
            s.content for s in sections
            if s.heading not in AUTO_SECTION_HEADINGS and (s.content or "").strip()
        )

    def _run_citation_check(self, ctx: AgentContext, sections: List[ReportSection]) -> dict:
        """校验正文数字能否在它自己引用的检索资料中找到，并登记到 ctx。

        sources 的编号必须与 ``report.references`` 完全一致：
        writer 就是按 ``enumerate(ctx.search_results, start=1)`` 建 Reference 的，
        这里保持同一编号口径，否则会把“引用错位”误报成“编造数据”。
        """
        sources = [
            {
                "index": i,
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "content": r.fetched_content,
            }
            for i, r in enumerate(ctx.search_results, start=1)
        ]
        rep = check_citations(self._body_text(sections), sources)

        # check_citations 会把 snippet 也算作“可校验正文”，但检索摘要只是搜索引擎的
        # 转述、未必含原文数字。为了不过度声称，只有真正抓到网页正文（fetched_content）
        # 时才判定为“已执行”；否则如实标注未执行（离线 mock 资料即属此类）。
        if rep.checked and not any(
            (r.fetched_content or "").strip() for r in ctx.search_results
        ):
            rep.checked = False
            rep.message = "检索资料没有正文内容（只有标题/链接），无法做引用溯源"

        if rep.checked:
            ctx.mark_check(
                "citation_grounding",
                "引用溯源校验（数字能否在引用来源中找到）",
                executed=True,
                passed=None,  # 信息性检查：给出覆盖率与可疑数字，不单独决定通过与否
                detail=(
                    f"覆盖率 {rep.coverage:.0%}（{rep.grounded}/{rep.total_numbers} 个数字"
                    f"可在引用来源中找到），疑似对不上 {len(rep.ungrounded)} 个，"
                    f"无引用标注 {rep.uncited} 个；覆盖 {rep.sources_count} 条来源"
                ),
            )
            ctx.log(
                f"[writer] 引用溯源校验：覆盖率 {rep.coverage:.0%}，"
                f"疑似对不上 {len(rep.ungrounded)} 个数字"
            )
        else:
            # 离线 mock 资料没有正文，如实标注“未执行”，不得声称已启用。
            ctx.mark_check(
                "citation_grounding",
                "引用溯源校验（数字能否在引用来源中找到）",
                executed=False,
                detail="校验报告正文中的财务数字能否在其引用的检索资料中找到。",
                reason=f"未执行：{rep.message}",
            )
            ctx.log(f"[writer] 引用溯源校验未执行：{rep.message}")
        return rep.to_dict()

    @staticmethod
    def _render_debate_section(debate) -> ReportSection:
        """3.0：把多空辩论结果渲染成一个对照章节（多空双方的论点与反驳都渲染）。"""
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
        # A3：两轮互相反驳都要渲染，并显式标明轮次，避免读者误以为只有一轮。
        if debate.bull_rebuttal:
            content += f"**第 1 轮 · 多方反驳（针对空方）**：{debate.bull_rebuttal}\n\n"
        if debate.bear_rebuttal:
            content += f"**第 1 轮 · 空方反驳（针对多方）**：{debate.bear_rebuttal}\n\n"
        bull_r2 = getattr(debate, "bull_rebuttal_round2", "")
        bear_r2 = getattr(debate, "bear_rebuttal_round2", "")
        if bull_r2:
            content += f"**第 2 轮 · 多方再反驳（针对空方第 1 轮反驳）**：{bull_r2}\n\n"
        if bear_r2:
            content += f"**第 2 轮 · 空方再反驳（针对多方第 1 轮反驳）**：{bear_r2}\n\n"
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

        # 论点为空的概率很低（离线兜底也会给出占位论点），
        # 万一为空也要显式说明，不能用 "—" 糊弄读者。
        bull_top = debate.bull_points[:3] or ["（本轮未提炼出多方论点，需人工复核）"]
        bear_top = debate.bear_points[:3] or ["（本轮未提炼出空方论点，需人工补充风险分析）"]

        lines = [
            "| 项目 | 内容 |",
            "| --- | --- |",
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

    # -- 反幻觉总览：只陈述真实执行过的校验 ---------------------------------
    # 下列校验属于“输入锁定 / 数字逐条校验 / 语义一致性 / 三表勾稽”，
    # 只有投研助手（公司速览）路径才会真正执行；主题研报没有标的公司事实库，
    # 因此在本路径下必须如实标注“未启用”并说明原因，绝不能盖可信度章。
    _DISABLED_CHECKS = {
        "input_constraint": (
            "第一道：输入约束（数字白名单）",
            "大模型只能使用已核实事实库中的数字。",
            "主题研报没有标的公司的已核实事实库（FactBase），无法把可用数字锁定为白名单。",
        ),
        "numeric_verify": (
            "第二道：数字校验（逐条比对真实数据）",
            "生成后提取报告中的数字，与真实数据逐条比对。",
            "主题研报没有标的公司事实库，无法逐条比对报告中的数字；该防线仅在投研助手公司速览路径启用。",
        ),
        "semantic_check": (
            "第三道：语义一致性校验",
            "检查定性判断/因果归因是否有事实包支撑。",
            "语义校验依赖事实包，仅在投研助手公司速览路径启用，本路径未执行。",
        ),
        "statements": (
            "附加：三大报表勾稽",
            "核对资产=负债+权益、净利润勾稽、现金流勾稽等会计恒等式。",
            "主题研报不含标的公司三大报表数据，未做勾稽校验。",
        ),
    }

    def _ensure_disabled_checks(self, ctx: AgentContext) -> None:
        """登记本路径下确实未执行的校验项（含未执行原因）。"""
        for key, (name, description, reason) in self._DISABLED_CHECKS.items():
            if key not in ctx.checks:
                ctx.mark_check(
                    key, name, executed=False, detail=description, reason=reason
                )
        # U5：引用溯源校验是主题研报下**真实可用**的防线，必须始终出现在总览中。
        # 若本轮尚未运行（例如直接渲染总览），如实登记为“未执行”，不得声称已启用。
        if "citation_grounding" not in ctx.checks:
            ctx.mark_check(
                "citation_grounding",
                "引用溯源校验（数字能否在引用来源中找到）",
                executed=False,
                detail="校验报告正文中的财务数字能否在其引用的检索资料中找到。",
                reason="未执行：本轮未运行引用溯源校验。",
            )

    def render_anti_hallucination(self, ctx: AgentContext) -> ReportSection:
        """3.5：报告结尾的反幻觉总览——只依据 ctx 中真实执行过的校验。"""
        self._ensure_disabled_checks(ctx)
        checks = list(ctx.checks.values())

        # 可信度必须由真实执行过的“数字类”校验决定：
        # 数字校验没跑，就没有资格给可信度评级。
        numeric_executed = any(
            c.executed for c in checks if c.key in ("numeric_verify", "input_constraint")
        )
        failed = [c for c in checks if c.executed and c.passed is False]

        if not numeric_executed:
            confidence = "未评级"
            confidence_note = (
                "数字校验未启用（本路径为行业/主题研报，没有标的公司的已核实事实库），"
                "因此不给出可信度等级。"
            )
        elif failed:
            confidence = "C（存在未通过校验项）"
            confidence_note = "部分校验未通过：" + "；".join(c.name for c in failed)
        else:
            confidence = "B（结构与引用校验通过，数字校验已执行且通过）"
            confidence_note = "数字校验已实际执行并通过。"

        lines = [
            "| 防线/校验 | 说明 | 状态 |",
            "| --- | --- | --- |",
        ]
        for record in checks:
            lines.append(
                f"| **{record.name}** | {record.detail or record.reason or '—'} | "
                f"{self._render_check_status(record)} |"
            )
        lines += [
            "",
            f"**本报告可信度：{confidence}**",
            "",
            f"> {confidence_note}",
            "> 本总览仅陈述本轮真实执行过的校验；标为“未启用”的项目未参与本次生成，"
            "不得据此推断报告可信度。",
        ]
        return ReportSection(
            heading="🛡️ 反幻觉总览",
            content="\n".join(lines),
            citations=[],
        )

    @staticmethod
    def _render_check_status(record) -> str:
        if not record.executed:
            return f"⚪ 未启用（{record.reason or '未说明原因'}）"
        if record.passed is True:
            detail = f"：{record.detail}" if record.detail else ""
            return f"✅ 已执行（通过{detail}）"
        if record.passed is False:
            return "❌ 已执行（未通过）"
        detail = record.detail if record.detail else "信息性检查"
        return f"✅ 已执行（{detail}）"

    def refresh_checks(self, ctx: AgentContext) -> None:
        """审校完成后用最新的真实校验记录刷新「反幻觉总览」章节。"""
        if ctx.report is None:
            return
        section = self.render_anti_hallucination(ctx)
        for index, existing in enumerate(ctx.report.sections):
            if existing.heading == section.heading:
                ctx.report.sections[index] = section
                break
        else:
            ctx.report.sections.append(section)
        ctx.report.meta["checks"] = {k: v.to_dict() for k, v in ctx.checks.items()}

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
