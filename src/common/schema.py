"""研报流水线中各阶段流转的数据结构定义。

全部使用标准库 dataclass，便于序列化（to_dict）与在智能体之间传递，
不强制依赖 pydantic / PyYAML 等第三方库。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class SearchResult:
    """单条检索结果。"""

    title: str
    url: str
    snippet: str = ""
    source: str = ""          # 检索渠道，如 duckduckgo / tavily / mock
    query: str = ""           # 触发该结果的检索词
    fetched_content: str = ""  # 可选：抓取到的网页正文（截断后）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SectionPlan:
    """报告单个章节的规划。"""

    heading: str              # 章节标题
    purpose: str = ""         # 该章节要回答的核心问题
    keywords: List[str] = field(default_factory=list)  # 检索关键词


@dataclass
class ResearchPlan:
    """规划智能体产出的研究提纲。"""

    topic: str
    objective: str = ""
    questions: List[str] = field(default_factory=list)   # 关键研究问题
    sections: List[SectionPlan] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)    # 全局检索关键词

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisNote:
    """分析智能体针对单个章节产出的分析要点。"""

    heading: str
    bullet_points: List[str] = field(default_factory=list)
    citation_indexes: List[int] = field(default_factory=list)  # 引用的资料编号


@dataclass
class ReportSection:
    """报告中的成稿章节。"""

    heading: str
    content: str = ""
    citations: List[int] = field(default_factory=list)


@dataclass
class ReviewResult:
    """审校智能体的结论。"""

    passed: bool
    score: int = 0                       # 0-100
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Reference:
    """参考文献条目。"""

    index: int
    title: str
    url: str
    source: str = ""


@dataclass
class ResearchReport:
    """最终研报产物。"""

    topic: str
    abstract: str = ""
    sections: List[ReportSection] = field(default_factory=list)
    references: List[Reference] = field(default_factory=list)
    language: str = "zh"
    mode: str = "offline"                # online / offline，标记是否连接真实模型与检索
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentContext:
    """在整条智能体流水线中共享的上下文对象。"""

    topic: str
    plan: Optional[ResearchPlan] = None
    search_results: List[SearchResult] = field(default_factory=list)
    notes: List[AnalysisNote] = field(default_factory=list)
    report: Optional[ResearchReport] = None
    review: Optional[ReviewResult] = None
    trace: List[str] = field(default_factory=list)  # 流水线执行轨迹（人类可读）

    def log(self, message: str) -> None:
        self.trace.append(message)
