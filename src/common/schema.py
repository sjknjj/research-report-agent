"""研报流水线中各阶段流转的数据结构定义。

全部使用标准库 dataclass，便于序列化（to_dict）与在智能体之间传递，
不强制依赖 pydantic / PyYAML 等第三方库。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# 由流水线自动生成、并非来自检索证据的章节标题：
# 统计引用覆盖率时必须排除，否则永远无法达标（人为制造“不能通过”的审校）。
AUTO_SECTION_HEADINGS = ("速览仪表盘", "多空对照与综合判断", "🛡️ 反幻觉总览")


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
class DebateResult:
    """多空辩论结果（3.0新增）。"""

    bull_points: List[str] = field(default_factory=list)     # 乐观论点
    bear_points: List[str] = field(default_factory=list)      # 谨慎/利空论点
    bull_rebuttal: str = ""      # 多方反驳空方
    bear_rebuttal: str = ""      # 空方反驳多方
    synthesis: str = ""          # 综合平衡观点
    score: int = 0               # 多空评分（0=极度看空，10=极度看多，5=中性）

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
class CheckRecord:
    """流水线中某一项校验/防线的真实执行记录。

    反幻觉总览必须由这里的真实记录驱动：
    - ``executed=True`` 才允许对外声明“已启用”；
    - ``executed=False`` 时必须填写 ``reason``，说明为什么没跑，
      报告中要如实标注“未启用”，不得伪造可信度。
    """

    key: str                       # 校验项标识
    name: str                      # 面向读者的名称
    executed: bool = False         # 本轮是否真的执行过
    passed: Optional[bool] = None  # 执行结果；None 表示“信息性检查”（如覆盖率）
    detail: str = ""               # 执行细节（覆盖率、评分等）
    reason: str = ""               # 未执行原因（executed=False 时必填）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentContext:
    """在整条智能体流水线中共享的上下文对象。"""

    topic: str
    plan: Optional[ResearchPlan] = None
    search_results: List[SearchResult] = field(default_factory=list)
    notes: List[AnalysisNote] = field(default_factory=list)
    debate: Optional[DebateResult] = None       # 3.0：多空辩论结果
    report: Optional[ResearchReport] = None
    review: Optional[ReviewResult] = None
    checks: Dict[str, CheckRecord] = field(default_factory=dict)  # 真实执行过的校验记录
    trace: List[str] = field(default_factory=list)  # 流水线执行轨迹（人类可读）

    def log(self, message: str) -> None:
        self.trace.append(message)

    def mark_check(
        self,
        key: str,
        name: str,
        *,
        executed: bool,
        passed: Optional[bool] = None,
        detail: str = "",
        reason: str = "",
    ) -> CheckRecord:
        """登记一项校验的真实执行状态（未执行必须给出原因）。"""
        record = CheckRecord(
            key=key,
            name=name,
            executed=executed,
            passed=passed,
            detail=detail,
            reason=reason,
        )
        self.checks[key] = record
        return record
