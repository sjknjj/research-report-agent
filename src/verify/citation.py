"""引用溯源校验（反幻觉升级 U5）。

背景：
    反幻觉的四道防线原本只在「公司速览 / 公告提炼」路径生效——那两条路径有明确的
    标的公司，可以构建 ``FactBase``。而「深度研报」是主题研究，没有事实库，
    于是它的数字**完全没有被检查**，只在报告结尾写一句"数字校验未启用"。

本模块给深度研报补上一道**确定性**防线：
    **报告里的数字，必须能在它自己引用的检索资料里找到。**

    抓的是「编造引用 / 数字与来源对不上」这类幻觉——例如文中写"营收 152.3 亿元[3]"，
    但来源 [3] 里根本没有 152.3 这个数字。

与 ``verifier`` 的分工：
    - verifier：数字 ↔ 事实库（有标的公司时可用，精度最高）
    - citation：数字 ↔ 引用来源（主题研报可用，覆盖面更广）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from ..common.logger import get_logger
from .numbers import KIND_BARE, extract_numbers, find_in_text, format_number

logger = get_logger("verify.citation")

# 句子切分（保留标点，便于定位）
_SENTENCE_RE = re.compile(r"[^。！？；\n]+[。！？；]?")
# 引用标注：[1] [1,2] [1-3] [1、2]
_CITE_RE = re.compile(r"\[\s*(\d+(?:\s*[,\-–、]\s*\d+)*)\s*\]")


@dataclass
class CitationReport:
    """引用溯源校验结果。"""

    checked: bool = False
    total_numbers: int = 0          # 正文中的财务数字总数
    grounded: int = 0               # 能在引用来源中找到的数字数
    uncited: int = 0                # 所在句子完全没有引用标注的数字数
    ungrounded: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""
    sources_count: int = 0

    @property
    def coverage(self) -> float:
        if not self.total_numbers:
            return 0.0
        return round(self.grounded / self.total_numbers, 3)

    @property
    def ungrounded_ratio(self) -> float:
        if not self.total_numbers:
            return 0.0
        return round(len(self.ungrounded) / self.total_numbers, 3)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checked": self.checked,
            "total_numbers": self.total_numbers,
            "grounded": self.grounded,
            "uncited": self.uncited,
            "ungrounded": self.ungrounded[:20],
            "coverage": self.coverage,
            "ungrounded_ratio": self.ungrounded_ratio,
            "sources_count": self.sources_count,
            "message": self.message,
        }

    def render_text(self) -> str:
        if not self.checked:
            return f"引用溯源校验：未执行（{self.message or '无可用的检索资料正文'}）"
        head = (
            f"引用溯源校验：正文 {self.total_numbers} 个数字，"
            f"可在引用来源中找到 {self.grounded} 个（覆盖率 {self.coverage:.0%}），"
            f"无引用标注 {self.uncited} 个，疑似编造/对不上 {len(self.ungrounded)} 个"
        )
        lines = [head]
        for item in self.ungrounded[:10]:
            lines.append(
                f"  [✗] 数字 {item['number']} 在引用来源 {item['citation']} 中找不到"
                f"（{item['snippet']}）"
            )
        return "\n".join(lines)


def _parse_citations(marker: str) -> List[int]:
    """把 ``1,2`` / ``1-3`` / ``1、2`` 解析成引用序号列表。"""
    out: List[int] = []
    for part in re.split(r"[,\-–、]", marker):
        part = part.strip()
        if not part.isdigit():
            continue
        out.append(int(part))
    return out


def _expand_ranges(marker: str) -> List[int]:
    """支持 ``[1-3]`` 这种区间写法。"""
    m = re.fullmatch(r"\s*(\d+)\s*[-–]\s*(\d+)\s*", marker)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a <= b and b - a <= 20:
            return list(range(a, b + 1))
    return _parse_citations(marker)


def check_citations(
    text: str,
    sources: Sequence[Dict[str, Any]],
    rel_tol: float = 0.01,
    pct_tol: float = 0.1,
) -> CitationReport:
    """校验正文数字是否能被其引用的来源支撑。

    :param text: 待校验正文（建议只传正文章节，不含自动生成的仪表盘）
    :param sources: 检索资料列表，元素形如
        ``{"index": 1, "title": "...", "url": "...", "content": "正文..."}``；
        ``index`` 缺省时按列表顺序编号（从 1 开始）。
    """
    report = CitationReport()
    if not text or not text.strip():
        report.message = "正文为空"
        return report

    source_texts: Dict[int, str] = {}
    has_body = False
    for pos, src in enumerate(sources or [], start=1):
        if not isinstance(src, dict):
            continue
        idx = src.get("index")
        try:
            idx = int(idx) if idx is not None else pos
        except (TypeError, ValueError):
            idx = pos
        body_only = " ".join(
            str(src.get(k, "") or "") for k in ("content", "snippet", "text")
        ).strip()
        if body_only:
            has_body = True
        # 检索时把标题也纳入匹配范围，但"能否校验"取决于有没有正文
        searchable = " ".join(
            str(src.get(k, "") or "") for k in ("title", "content", "snippet", "text")
        ).strip()
        if searchable:
            source_texts[idx] = searchable

    report.sources_count = len(source_texts)
    if not source_texts or not has_body:
        report.message = "检索资料没有正文内容（只有标题/链接），无法做引用溯源"
        return report

    report.checked = True
    for sm in _SENTENCE_RE.finditer(text):
        sentence = sm.group(0)
        numbers = [n for n in extract_numbers(sentence) if n.kind != KIND_BARE]
        if not numbers:
            continue
        cited: List[int] = []
        for cm in _CITE_RE.finditer(sentence):
            cited.extend(_expand_ranges(cm.group(1)))
        cited = sorted({i for i in cited if i in source_texts})

        for n in numbers:
            report.total_numbers += 1
            if not cited:
                report.uncited += 1
                continue
            hit = any(find_in_text(source_texts[i], n, rel_tol, pct_tol) for i in cited)
            if hit:
                report.grounded += 1
            else:
                report.ungrounded.append({
                    "number": n.raw,
                    "value": format_number(n.value, n.kind),
                    "citation": cited,
                    "snippet": sentence.strip()[:80],
                })

    report.message = (
        f"覆盖率 {report.coverage:.0%}"
        if report.total_numbers else "正文中没有可校验的财务数字"
    )
    return report
