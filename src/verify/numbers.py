"""通用数字抽取与比较（反幻觉升级 U1 / U5 的公共内核）。

为什么需要它：
    旧版 ``CrossVerifier`` 只能检查"命中指标词典的 18 个指标"。报告里出现的其它
    数字（研发投入、合同金额、人数、市占率……）会被**静默放过**，让人误以为
    报告已被完整校验。本模块提供与词典无关的"全量数字抽取"，供：

    - ``verifier``：把报告里**每个**数字归因为
      ``supported`` / ``mismatch`` / ``unsourced``（无来源），而不是只看词典命中项；
    - ``citation``：深度研报路径的引用溯源校验——报告里的数字必须能在其引用的
      检索资料中找到。

设计原则：
    - 只依赖标准库，确定性、可离线运行；
    - 金额统一归一到 **元**，比率为**百分数数值**，倍数为倍数；
    - 明确区分"财务数字"与"普通计数/年份/代码"，后者不计入覆盖率分母，
      避免用"第 3 名""5 家"之类噪声污染可信度。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

_YI, _WAN, _WANYI = 1e8, 1e4, 1e12

# 数值本体：支持正负号与千分位
_NUM = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"

# 一个组合正则，按"单位"分流；单位可有可无
_TOKEN_RE = re.compile(
    r"(?P<num>" + _NUM + r")"
    r"(?:\s*(?:"
    r"(?P<amount_unit>万亿|亿|万)\s*(?:元|人民币)?"
    r"|(?P<pct>个百分点|%|％)"
    r"|(?P<mult>倍)"
    r"|(?P<price_unit>元|港元|美元|人民币)"
    r"))?"
)

_YEAR_SUFFIX_RE = re.compile(r"\s*年")
_CODE_RE = re.compile(r"\d{6}")

# 数值种类
KIND_AMOUNT = "amount"      # 金额（归一到元）
KIND_PERCENT = "percent"    # 百分比数值
KIND_MULTIPLE = "multiple"  # 倍数（PE/PB 等）
KIND_PRICE = "price"        # 价格（元）
KIND_BARE = "bare"          # 无单位普通数字/计数，不计入覆盖率

_FINANCIAL_KINDS = (KIND_AMOUNT, KIND_PERCENT, KIND_MULTIPLE, KIND_PRICE)


@dataclass
class NumberMention:
    """文本中的一个数字出现。"""

    raw: str            # 原文片段，如 "152.30亿元"
    literal: str        # 纯数字字面量，如 "152.30"
    value: float        # 归一化数值（金额->元，比率->百分数）
    kind: str
    start: int          # 数字字面量在原文中的起始下标
    end: int            # 结束下标（不含）
    context: str = ""   # 前后文窗口，供指标归因/引用判断使用

    @property
    def is_financial(self) -> bool:
        return self.kind in _FINANCIAL_KINDS

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "value": self.value,
            "kind": self.kind,
            "start": self.start,
            "context": self.context,
        }


def _to_float(literal: str) -> Optional[float]:
    try:
        return float(str(literal).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _unit_multiplier(unit: str) -> float:
    return {"万亿": _WANYI, "亿": _YI, "万": _WAN}[unit]


def extract_numbers(text: str, context_window: int = 32) -> List[NumberMention]:
    """抽取文本中所有数字，并做种类归一。

    - 4 位年份（``2025年``）、6 位代码（``600519``）等无单位数字会被识别为
      ``bare``；调用方据此把它们排除在覆盖率之外。
    - 金额统一换算成**元**；百万/亿/万亿都支持。
    """
    if not text:
        return []
    out: List[NumberMention] = []
    for m in _TOKEN_RE.finditer(text):
        literal = m.group("num")
        value = _to_float(literal)
        if value is None:
            continue

        amount_unit = m.group("amount_unit")
        pct = m.group("pct")
        mult = m.group("mult")
        price_unit = m.group("price_unit")

        if amount_unit:
            kind = KIND_AMOUNT
            value = value * _unit_multiplier(amount_unit)
        elif pct:
            kind = KIND_PERCENT
        elif mult:
            kind = KIND_MULTIPLE
        elif price_unit:
            kind = KIND_PRICE
        else:
            # 无单位：年份/股票代码等噪声识别为 bare
            after = text[m.end(): m.end() + 2]
            digits = re.sub(r"[-+,]", "", literal)
            if _YEAR_SUFFIX_RE.fullmatch(after) and len(digits) == 4:
                kind = KIND_BARE
            elif _CODE_RE.fullmatch(digits):
                kind = KIND_BARE
            else:
                kind = KIND_BARE

        start, end = m.start("num"), m.end("num")
        ctx_start = max(0, start - context_window)
        ctx_end = min(len(text), end + context_window)
        out.append(NumberMention(
            raw=m.group(0).strip(),
            literal=literal,
            value=value,
            kind=kind,
            start=start,
            end=end,
            context=text[ctx_start:ctx_end],
        ))
    return out


def numbers_match(
    a: NumberMention,
    b: NumberMention,
    rel_tol: float = 0.01,
    pct_tol: float = 0.1,
) -> bool:
    """判断两个数字是否"数值一致"（用于归因与引用溯源）。

    - 只在同类或可兼容种类间比较（金额 vs 金额、比率 vs 比率、价格 vs 倍数按数值比）；
    - 比率用百分点绝对容差，其余用相对容差。
    """
    if a is None or b is None:
        return False
    # bare 只做字面量比较
    if a.kind == KIND_BARE or b.kind == KIND_BARE:
        return re.sub(r"[-+,]", "", a.literal) == re.sub(r"[-+,]", "", b.literal)

    compatible = {a.kind, b.kind} <= {KIND_PRICE, KIND_MULTIPLE} or a.kind == b.kind
    if not compatible:
        return False

    if a.kind == KIND_PERCENT and b.kind == KIND_PERCENT:
        return abs(a.value - b.value) <= pct_tol + 1e-9

    denom = abs(b.value) or 1e-9
    return abs(a.value - b.value) / denom <= rel_tol + 1e-9


def find_in_text(haystack: str, needle: NumberMention,
                 rel_tol: float = 0.01, pct_tol: float = 0.1) -> bool:
    """``needle`` 的数值是否能在 ``haystack`` 中被找到（引用溯源用）。"""
    if not haystack:
        return False
    for candidate in extract_numbers(haystack):
        if candidate.kind == KIND_BARE:
            continue
        if numbers_match(needle, candidate, rel_tol=rel_tol, pct_tol=pct_tol):
            return True
    return False


def format_number(value: float, kind: str) -> str:
    """把归一化数值还原为可读文本（用于纠错提示）。"""
    if kind == KIND_AMOUNT:
        if abs(value) >= _YI:
            return f"{value / _YI:.2f} 亿元"
        if abs(value) >= _WAN:
            return f"{value / _WAN:.2f} 万元"
        return f"{value:.2f} 元"
    if kind == KIND_PERCENT:
        return f"{value:.2f}%"
    if kind == KIND_MULTIPLE:
        return f"{value:.2f} 倍"
    return f"{value:.2f}"
