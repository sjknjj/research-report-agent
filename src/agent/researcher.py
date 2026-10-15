"""检索智能体：根据提纲生成检索词、汇总资料并按需抓取网页正文。

支持两种模式：
- 普通模式：一次性按提纲关键词检索
- 深挖模式（deep）：初检 → LLM 分析知识缺口 → 生成补充检索词 → 再检索 → 最多 N 轮
"""
from __future__ import annotations

import json
from typing import List

from ..common.logger import get_logger
from ..common.schema import AgentContext
from ..tools.fetcher import fetch_page
from ..tools.search import SearchTool
from .base import BaseAgent

logger = get_logger("agent.researcher")

_GAP_PROMPT = (
    "你是研究主管。以下是针对「{topic}」已检索到的资料摘要，"
    "以及研究提纲。请判断：为了写出高质量研报，还有哪些关键信息缺口？"
    "列出 2-4 个补充检索问题（每个问题是一句话、可直接用于搜索引擎的中文查询）。"
    "严格输出 JSON：{{\"follow_up_queries\": string[]}}。"
    "如果现有资料已足够，返回空数组。\n\n"
    "研究提纲：{plan_summary}\n\n"
    "已检索资料（前 {n} 条标题）：\n{titles}\n"
)


class ResearcherAgent(BaseAgent):
    role_name = "researcher"

    MAX_QUERIES = 10

    def run(self, ctx: AgentContext, search_tool: SearchTool = None,
             deep: bool = False, deep_rounds: int = 2) -> None:
        if search_tool is None:
            raise RuntimeError("ResearcherAgent 需要传入 SearchTool")
        if ctx.plan is None:
            raise RuntimeError("检索阶段缺少研究提纲")

        queries = self._build_queries(ctx)
        self.logger.info("生成 %d 条检索词：%s", len(queries),
                         "；".join(queries[:5]) + " …")

        max_total = min(20, max(8, len(ctx.plan.sections) * 3))
        results = search_tool.search_many(queries, max_total=max_total)
        self.logger.info("去重后获得 %d 条资料", len(results))

        # 递归深挖：分析缺口 → 补充检索
        if deep and not self.settings.is_offline:
            for rnd in range(1, deep_rounds + 1):
                follow_ups = self._find_gaps(ctx, results)
                if not follow_ups:
                    self.logger.info("第 %d 轮：无明显知识缺口，停止深挖", rnd)
                    break
                self.logger.info("第 %d 轮深挖：补充检索 %s", rnd, "；".join(follow_ups))
                new_results = search_tool.search_many(follow_ups, max_total=10)
                existing_urls = {r.url for r in results}
                added = 0
                for item in new_results:
                    if item.url not in existing_urls:
                        results.append(item)
                        existing_urls.add(item.url)
                        added += 1
                self.logger.info("第 %d 轮新增 %d 条资料（累计 %d）", rnd, added, len(results))

        is_mock = getattr(search_tool.provider, "name", "") == "mock"
        if not is_mock and self.settings.max_fetch_pages > 0:
            self._enrich_with_fetched_content(results)
        elif is_mock:
            self.logger.info("当前为离线占位资料，跳过网页正文抓取")

        ctx.search_results = results
        ctx.log(f"[researcher] 检索到 {len(results)} 条资料"
                f"{'（深挖模式）' if deep else ''}")

    def _find_gaps(self, ctx: AgentContext, results) -> List[str]:
        """让 LLM 分析知识缺口，返回补充检索词。"""
        plan = ctx.plan
        plan_summary = "；".join(
            f"{s.heading}（{s.purpose}）" for s in plan.sections
        )
        titles = "\n".join(f"- {r.title}" for r in results[:15])
        prompt = _GAP_PROMPT.format(
            topic=ctx.topic,
            plan_summary=plan_summary,
            n=len(results),
            titles=titles,
        )
        try:
            data = self.llm.chat_json(
                [{"role": "user", "content": prompt}],
                stage="gap_analysis",
                temperature=0.2,
            )
            queries = [str(q) for q in data.get("follow_up_queries", []) if q]
            return queries[:4]
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("缺口分析失败，跳过本轮深挖：%s", exc)
            return []

    def _build_queries(self, ctx: AgentContext) -> List[str]:
        plan = ctx.plan
        queries: List[str] = []
        seen = set()

        def add(q: str) -> None:
            q = q.strip()
            if q and q not in seen:
                seen.add(q)
                queries.append(q)

        for kw in plan.keywords:
            add(kw)
        for section in plan.sections:
            for kw in section.keywords:
                add(kw if len(kw) >= 4 else f"{ctx.topic} {kw}")
        for question in plan.questions[:2]:
            add(question)

        return queries[: self.MAX_QUERIES]

    def _enrich_with_fetched_content(self, results) -> None:
        """对排名靠前的检索结果抓取正文，作为分析证据。"""
        fetched = 0
        for item in results:
            if fetched >= self.settings.max_fetch_pages:
                break
            content = fetch_page(
                item.url,
                max_chars=self.settings.fetch_content_chars,
                timeout=15,
            )
            if content:
                item.fetched_content = content
                fetched += 1
        self.logger.info("成功抓取 %d 个网页正文", fetched)
