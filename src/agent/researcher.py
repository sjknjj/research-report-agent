"""检索智能体：根据提纲生成检索词、汇总资料并按需抓取网页正文。

支持两种模式：
- 普通模式：一次性按提纲关键词检索
- 深挖模式（deep）：初检 → LLM 分析知识缺口 → 生成补充检索词 → 再检索 → 最多 N 轮
  （N 来自 ``settings.deep_search_rounds``，离线模式下无法做缺口分析，会显式提示）
"""
from __future__ import annotations

from typing import List, Optional

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

    def run(
        self,
        ctx: AgentContext,
        search_tool: SearchTool = None,
        deep: bool = False,
        deep_rounds: Optional[int] = None,
    ) -> None:
        if search_tool is None:
            raise RuntimeError("ResearcherAgent 需要传入 SearchTool")
        if ctx.plan is None:
            raise RuntimeError("检索阶段缺少研究提纲")

        rounds = self._resolve_deep_rounds(deep_rounds)
        queries = self._build_queries(ctx)
        self.logger.info("生成 %d 条检索词：%s", len(queries),
                         "；".join(queries[:5]) + " …")

        max_total = min(20, max(8, len(ctx.plan.sections) * 3))
        results = search_tool.search_many(queries, max_total=max_total)
        self.logger.info("去重后获得 %d 条资料", len(results))

        # 递归深挖：分析缺口 → 补充检索
        if deep and self.settings.is_offline:
            # 不能静默失效：离线时无法调用大模型做缺口分析，必须给出用户可见提示
            hint = ("离线演示模式下深挖检索不可用（无法调用大模型分析知识缺口），"
                    "已退化为单轮检索。配置 LLM_API_KEY 后 --deep 才会生效。")
            self.logger.warning(hint)
            ctx.log(f"[researcher] {hint}")
        elif deep:
            for rnd in range(1, rounds + 1):
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

    def _resolve_deep_rounds(self, override: Optional[int]) -> int:
        """深挖轮数：显式传参优先，否则读取 settings.deep_search_rounds。"""
        raw = override if override is not None else getattr(self.settings, "deep_search_rounds", 2)
        rounds = self._to_int(raw, 2)
        return max(0, rounds)

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
            return self._to_str_list(data.get("follow_up_queries"))[:4]
        except Exception as exc:  # noqa: BLE001
            # 认证/配置类错误必须上抛，不能装作“没有缺口”
            self._raise_if_fatal(exc)
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
        """对排名靠前的检索结果抓取正文，作为分析证据。

        ``max_fetch_pages`` 限制的是**尝试抓取的网页数**（而非成功数）：
        否则遇到失效链接时会一路尝试到结果列表末尾，远超配置上限。
        """
        attempts = 0
        fetched = 0
        max_pages = max(0, self._to_int(self.settings.max_fetch_pages, 0))
        for item in results:
            if attempts >= max_pages:
                break
            attempts += 1
            content = fetch_page(
                item.url,
                max_chars=self.settings.fetch_content_chars,
                timeout=15,
            )
            if content:
                item.fetched_content = content
                fetched += 1
        self.logger.info("抓取尝试 %d 个网页，成功 %d 个", attempts, fetched)
