"""联网检索工具。

提供统一的检索接口与多种实现：
- :class:`MockSearchProvider`      离线占位（不访问网络，结果显式标注为占位，不伪造真实来源）
- :class:`DuckDuckGoSearchProvider` 基于 DuckDuckGo HTML 端点，无需密钥
- :class:`TavilySearchProvider`     基于 Tavily API，需要 TAVILY_API_KEY
- :class:`MultiSearchProvider`      auto 模式下的多引擎顺序回退（惰性探测，构造期不联网）

:func:`build_search_tool` 会根据配置自动选择，并在网络失败时安全回退，
保证整条流水线不会因为单个检索渠道异常而中断。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, List, Optional

from ..common.logger import get_logger
from ..common.schema import SearchResult

logger = get_logger("tools.search")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#x27;", "'")
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return re.sub(r"\s+", " ", text).strip()


class BaseSearchProvider(ABC):
    """检索渠道抽象基类。"""

    name: str = "base"

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        raise NotImplementedError


class MockSearchProvider(BaseSearchProvider):
    """离线占位检索：不联网，结果明确标注为占位。"""

    name = "mock"

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        results: List[SearchResult] = []
        for i in range(1, min(max_results, 3) + 1):
            results.append(
                SearchResult(
                    title=f"[离线占位 {i}] 关于「{query}」的示例资料位",
                    url=f"https://example.com/offline/{urllib.parse.quote(query)}/{i}",
                    snippet=(
                        "该条目并非真实检索结果，仅用于在离线模式下跑通流水线；"
                        "在线模式会替换为真实网页资料。"
                    ),
                    source=self.name,
                    query=query,
                )
            )
        return results


class MultiSearchProvider(BaseSearchProvider):
    """多引擎顺序回退渠道（auto 模式用）。

    **构造期不发任何网络请求**，首次 ``search`` 时才逐个真实探测，
    选中第一个返回结果的渠道并在后续调用中优先复用它；
    某个渠道失败或无结果会自动尝试下一个，因此 DuckDuckGo 等后置渠道是可迭代到的。
    """

    name = "auto"

    def __init__(self, providers: List[BaseSearchProvider]):
        self.providers = list(providers)
        self._active: Optional[BaseSearchProvider] = None

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        ordered = ([self._active] if self._active else []) + [
            p for p in self.providers if p is not self._active
        ]
        for provider in ordered:
            try:
                results = provider.search(query, max_results)
            except Exception as exc:  # noqa: BLE001 - 单个渠道异常不能中断回退链
                logger.warning("检索渠道 %s 不可用：%s", provider.name, exc)
                continue
            if results:
                if provider is not self._active:
                    logger.info("检索渠道已选定：%s", provider.name)
                    self._active = provider
                return results
            logger.info("检索渠道 %s 无结果，尝试下一个", provider.name)
        return []


class DuckDuckGoSearchProvider(BaseSearchProvider):
    """基于 DuckDuckGo HTML 端点的免费检索（无需密钥）。"""

    name = "duckduckgo"
    endpoint = "https://html.duckduckgo.com/html/"

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=data,
            method="POST",
            headers={"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        blocks = re.split(r'<div class="result\s', html)
        results: List[SearchResult] = []
        for block in blocks[1:]:
            if len(results) >= max_results:
                break
            link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link:
                continue
            raw_href, raw_title = link.group(1), link.group(2)
            url = self._decode_redirect(raw_href)
            title = _strip_tags(raw_title)
            snippet_match = re.search(
                r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL
            )
            snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
            if title and url.startswith("http"):
                results.append(
                    SearchResult(
                        title=title, url=url, snippet=snippet, source=self.name, query=query
                    )
                )
        return results

    @staticmethod
    def _decode_redirect(href: str) -> str:
        """把 DuckDuckGo 跳转链接还原为真实 URL（无法还原时返回空串，避免把跳转页当来源）。"""
        if not href:
            return ""
        href = href.strip()
        if "uddg=" not in href:
            # 协议相对链接 //example.com/a 要补上 scheme，否则会被 startswith("http") 丢弃
            return "https:" + href if href.startswith("//") else href
        # 形如 //duckduckgo.com/l/?uddg=https%3A%2F%2F...&rut=...
        query_string = href.split("?", 1)[1] if "?" in href else href
        parsed = urllib.parse.parse_qs(query_string)
        values = parsed.get("uddg") or []
        if not values or not str(values[0]).strip():
            # uddg 为空：绝不回退成 DDG 自己的跳转链接
            return ""
        decoded = urllib.parse.unquote(str(values[0]).strip())
        return "https:" + decoded if decoded.startswith("//") else decoded


class BaiduSearchProvider(BaseSearchProvider):
    """基于百度搜索的免费检索（无需密钥，国内可用）。"""

    name = "baidu"
    endpoint = "https://www.baidu.com/s"

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        url = f"{self.endpoint}?wd={urllib.parse.quote(query)}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": _UA},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:  # noqa: BLE001
            logger.warning("百度搜索请求失败：%s", exc)
            return []

        results: List[SearchResult] = []
        # 百度搜索结果块
        blocks = re.split(r'<div class="result\s', html)
        for block in blocks[1:]:
            if len(results) >= max_results:
                break
            link = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link:
                continue
            raw_href, raw_title = link.group(1), link.group(2)
            title = _strip_tags(raw_title)
            # 百度会轮换混淆类名，摘要正则放宽为“任意 span”，抓不到就算了
            snippet_match = re.search(r'<span[^>]*>(.*?)</span>', block, re.DOTALL)
            snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
            if title and raw_href.startswith("http"):
                results.append(
                    SearchResult(
                        title=title, url=raw_href, snippet=snippet, source=self.name, query=query
                    )
                )
        return results


class BingSearchProvider(BaseSearchProvider):
    """基于必应搜索的免费检索（无需密钥，国内可用）。"""

    name = "bing"
    endpoint = "https://www.bing.com/search"

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        url = f"{self.endpoint}?q={urllib.parse.quote(query)}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": _UA},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:  # noqa: BLE001
            logger.warning("必应搜索请求失败：%s", exc)
            return []

        results: List[SearchResult] = []
        # 必应搜索结果块
        blocks = re.split(r'<li class="b_algo"\s', html)
        for block in blocks[1:]:
            if len(results) >= max_results:
                break
            link = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link:
                continue
            raw_href, raw_title = link.group(1), link.group(2)
            title = _strip_tags(raw_title)
            snippet_match = re.search(r'<p[^>]*class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
            snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
            if title and raw_href.startswith("http"):
                results.append(
                    SearchResult(
                        title=title, url=raw_href, snippet=snippet, source=self.name, query=query
                    )
                )
        return results


class TavilySearchProvider(BaseSearchProvider):
    """基于 Tavily Search API 的检索（质量更高，需要密钥）。"""

    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        results: List[SearchResult] = []
        for item in body.get("results", [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    source=self.name,
                    query=query,
                )
            )
        return results


class SearchTool:
    """对上层智能体暴露的检索工具，封装多查询、去重与安全回退。"""

    def __init__(self, provider: BaseSearchProvider, per_query: int = 5):
        self.provider = provider
        self.per_query = per_query

    def search_one(self, query: str) -> List[SearchResult]:
        # provider 可能抛出 JSONDecodeError / KeyError / 任意异常，
        # 这里统一兜底，兑现“安全回退”承诺
        try:
            results = self.provider.search(query, self.per_query)
            if results:
                return results
            logger.warning("渠道 %s 对「%s」无结果，回退到离线占位", self.provider.name, query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("渠道 %s 检索失败，已回退到离线占位：%s", self.provider.name, exc)
        return MockSearchProvider().search(query, self.per_query)

    def search_many(
        self,
        queries: List[str],
        max_total: Optional[int] = None,
    ) -> List[SearchResult]:
        """对多个检索词检索并按 URL 去重；``max_total=0`` 合法表示不返回结果。"""
        if max_total is not None and max_total <= 0:
            return []
        merged: List[SearchResult] = []
        seen = set()
        for query in queries:
            if not query:
                continue
            for item in self.search_one(query):
                key = item.url
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
            if max_total is not None and len(merged) >= max_total:
                break
        return merged[:max_total] if max_total is not None else merged


def build_search_tool(settings: Any) -> SearchTool:
    """依据配置构建检索工具（含 auto 选择逻辑）。

    注意：``auto`` 模式**不在构造期发起网络探测**（避免 MCP/orchestrator 每次新建都卡 15s），
    而是返回惰性的 :class:`MultiSearchProvider`，首次检索时才逐个真实探测。
    """
    provider_name = (settings.search_provider or "auto").lower()

    if provider_name == "mock":
        provider: BaseSearchProvider = MockSearchProvider()
    elif provider_name == "tavily":
        if settings.tavily_api_key:
            provider = TavilySearchProvider(settings.tavily_api_key)
        else:
            logger.warning("指定 tavily 但缺少 TAVILY_API_KEY，回退到离线占位。")
            provider = MockSearchProvider()
    elif provider_name == "duckduckgo":
        provider = DuckDuckGoSearchProvider()
    elif provider_name == "baidu":
        provider = BaiduSearchProvider()
    elif provider_name == "bing":
        provider = BingSearchProvider()
    else:  # auto
        if settings.effective_mode == "offline":
            provider = MockSearchProvider()
        elif settings.tavily_api_key:
            provider = TavilySearchProvider(settings.tavily_api_key)
        else:
            # 国内用户优先用百度（不用翻墙），失败后逐个回退到必应 / DuckDuckGo
            provider = MultiSearchProvider(
                [
                    BaiduSearchProvider(),
                    BingSearchProvider(),
                    DuckDuckGoSearchProvider(),
                ]
            )

    logger.info("检索渠道：%s", provider.name)
    return SearchTool(provider, per_query=settings.max_results_per_query)
