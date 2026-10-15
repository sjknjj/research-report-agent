"""交叉校验器（反幻觉第二道防线——输出侧逐条核对）。

流程：
1. 从大模型生成文本中抽取“数值声明”（营收、净利润、同比、毛利率、ROE、EPS、股价…）；
2. 与 :class:`~src.verify.facts.FactBase` 中由真实数据计算出的事实按容差比对；
3. 输出每条声明的状态：``supported``（一致）/ ``mismatch``（与事实矛盾，疑似幻觉）/
   ``unverifiable``（事实库未覆盖，无法证实）；
4. 生成纠错清单，可回填给大模型进行一次“基于核实值”的重写。

该校验是确定性的数值比对，不依赖模型自觉，从而真正拦截“一本正经地编数字”。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..common.logger import get_logger
from .facts import FactBase, fmt_amount, fmt_pct

logger = get_logger("verify.cross")

_YI, _WAN = 1e8, 1e4
# 容差
REL_TOL = 0.02          # 金额 / 价格相对容差 2%
PCT_TOL = 0.3           # 百分点绝对容差
PRICE_TOL = 0.02

_NUM = r"(-?\d+(?:\.\d+)?)"
_PCT_RE = re.compile(_NUM + r"\s*(?:个百分点|%)")
_AMOUNT_RE = re.compile(_NUM + r"\s*(万亿|亿|万)\s*元?")
_PRICE_RE = re.compile(_NUM + r"\s*元?")
_YEAR_RE = re.compile(r"(19|20)\d{2}\s*年")


@dataclass
class MetricSpec:
    key: str
    label: str
    kind: str                      # amount / percent / price
    aliases: List[str] = field(default_factory=list)
    yoy_anchor: bool = False       # 同比类：在关联指标窗口内找“同比…%”


# 指标词典：别名按“先长后短、先具体后宽泛”排列
METRICS: List[MetricSpec] = [
    MetricSpec("revenue", "营业总收入", "amount",
               ["营业总收入", "营业收入", "营收"]),
    MetricSpec("net_profit", "归母净利润", "amount",
               ["归属于上市公司股东的净利润", "归母净利润", "净利润", "净利"]),
    MetricSpec("revenue_yoy", "营业总收入同比增速", "percent",
               ["营业总收入同比", "营业收入同比", "营收同比", "营收增速",
                "营业总收入增长率", "营业收入增长率", "营收增长"], yoy_anchor=True),
    MetricSpec("net_profit_yoy", "归母净利润同比增速", "percent",
               ["归母净利润同比", "净利润同比", "净利同比", "净利润增速",
                "净利润增长"], yoy_anchor=True),
    MetricSpec("gross_margin", "销售毛利率", "percent", ["销售毛利率", "毛利率"]),
    MetricSpec("net_margin", "销售净利率", "percent", ["销售净利率", "净利率"]),
    MetricSpec("roe", "净资产收益率", "percent",
               ["加权平均净资产收益率", "净资产收益率", "ROE"]),
    MetricSpec("eps", "基本每股收益", "price", ["基本每股收益", "每股收益", "EPS"]),
    MetricSpec("latest_close", "最新收盘价", "price", ["最新收盘价", "收盘价", "股价"]),
    MetricSpec("latest_pct", "最新单日涨跌幅", "percent",
               ["最新单日涨跌幅", "当日涨跌幅", "单日涨跌幅"]),
    MetricSpec("period_return", "区间涨跌幅", "percent", ["区间涨跌幅", "累计涨跌幅"]),
    MetricSpec("annual_vol", "年化波动率", "percent", ["年化波动率", "波动率"]),
    MetricSpec("pe_ratio", "市盈率(PE)", "price",
               ["市盈率(PE-TTM)", "市盈率", "PE(TTM)", "PE"]),
    MetricSpec("pb_ratio", "市净率(PB)", "price",
               ["市净率(PB)", "市净率", "PB"]),
    MetricSpec("dividend_yield", "股息率", "percent", ["股息率"]),
    MetricSpec("main_net_inflow", "主力净流入", "amount",
               ["主力净流入净额", "主力净流入", "主力资金净流入"]),
]

# 同比指标关联的正指标锚点（用于在更大窗口内定位“同比…%”）
_YOY_ANCHORS = {
    "revenue_yoy": ["营业总收入", "营业收入", "营收"],
    "net_profit_yoy": ["归属于上市公司股东的净利润", "归母净利润", "净利润", "净利"],
}
_NEG_WORDS = ("下降", "下滑", "减少", "降低", "同比降")


@dataclass
class Claim:
    metric: str
    label: str
    kind: str
    claimed: float
    raw: str


@dataclass
class VerificationReport:
    claims: List[Dict[str, Any]] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    corrections: List[str] = field(default_factory=list)

    @property
    def n_supported(self) -> int:
        return sum(1 for c in self.claims if c["status"] == "supported")

    @property
    def n_mismatch(self) -> int:
        return sum(1 for c in self.claims if c["status"] == "mismatch")

    @property
    def n_unverifiable(self) -> int:
        return sum(1 for c in self.claims if c["status"] == "unverifiable")

    @property
    def hallucination_rate(self) -> float:
        if not self.claims:
            return 0.0
        return round(self.n_mismatch / len(self.claims), 3)

    @property
    def passed(self) -> bool:
        return self.n_mismatch == 0

    @property
    def confidence_level(self) -> str:
        """3.4新增：可信度等级（A=高可信 / B=中可信 / C=低可信 / D=不可信）。"""
        rate = self.hallucination_rate
        if rate < 0.05:
            return "A（高可信）"
        elif rate < 0.15:
            return "B（中可信）"
        elif rate < 0.30:
            return "C（低可信）"
        else:
            return "D（不可信）"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "confidence_level": self.confidence_level,
            "total_claims": len(self.claims),
            "supported": self.n_supported,
            "mismatch": self.n_mismatch,
            "unverifiable": self.n_unverifiable,
            "hallucination_rate": self.hallucination_rate,
            "claims": self.claims,
            "issues": self.issues,
            "corrections": self.corrections,
        }

    def render_text(self) -> str:
        lines = [
            f"交叉校验结论：{'通过' if self.passed else '未通过'}｜"
            f"可信度：{self.confidence_level}｜"
            f"共核对 {len(self.claims)} 处数值：一致 {self.n_supported}、"
            f"矛盾 {self.n_mismatch}、无法核实 {self.n_unverifiable}｜"
            f"疑似幻觉率 {self.hallucination_rate:.1%}",
        ]
        for c in self.claims:
            mark = {"supported": "✓", "mismatch": "✗", "unverifiable": "?"}[c["status"]]
            lines.append(f"  [{mark}] {c['label']}：{c['note']}")
        if self.corrections:
            lines.append("纠错（重写时必须采用下列核实值）：")
            lines.extend(f"  - {x}" for x in self.corrections)
        return "\n".join(lines)


class CrossVerifier:
    """从文本抽取数值声明并与事实库交叉核对。"""

    def __init__(self, rel_tol: float = REL_TOL, pct_tol: float = PCT_TOL):
        self.rel_tol = rel_tol
        self.pct_tol = pct_tol

    # ------------------------------------------------------------------
    def verify(self, text: str, fact_base: FactBase) -> VerificationReport:
        claims = self._extract_claims(text)
        report = VerificationReport()
        # 同一指标多处出现时，保留最差状态，避免重复计数
        worst: Dict[str, Dict[str, Any]] = {}

        for claim in claims:
            fact = fact_base.get(claim.metric)
            item = self._judge(claim, fact)
            existed = worst.get(claim.metric)
            rank = {"mismatch": 2, "unverifiable": 1, "supported": 0}
            if existed is None or rank[item["status"]] > rank[existed["status"]]:
                worst[claim.metric] = item

        report.claims = sorted(worst.values(), key=lambda x: x["metric"])
        for item in report.claims:
            if item["status"] == "mismatch":
                report.issues.append(f"{item['label']}：{item['note']}")
                report.corrections.append(item["correction"])
        return report

    # ------------------------------------------------------------------
    def _judge(self, claim: Claim, fact) -> Dict[str, Any]:
        base = {"metric": claim.metric, "label": claim.label, "claimed_raw": claim.raw}
        if fact is None or fact.value is None:
            return {**base, "status": "unverifiable", "claimed": claim.claimed,
                    "fact": None, "note": "事实库未覆盖该指标，无法证实（需人工/更多数据源确认）",
                    "correction": ""}
        if claim.kind == "percent":
            diff = abs(claim.claimed - fact.value)
            ok = diff <= self.pct_tol
        else:
            denom = abs(fact.value) or 1e-9
            diff = abs(claim.claimed - fact.value)
            ok = diff / denom <= self.rel_tol

        fact_text = self._display(claim.kind, fact.value)
        claim_text = self._display(claim.kind, claim.claimed)
        period = fact.period or "最新"
        if ok:
            return {**base, "status": "supported", "claimed": claim.claimed,
                    "fact": fact.value, "period": period,
                    "note": f"文本 {claim_text} ≈ 事实 {fact_text}（{period}），一致",
                    "correction": ""}
        return {**base, "status": "mismatch", "claimed": claim.claimed, "fact": fact.value,
                "period": period,
                "note": f"文本称 {claim_text}，但经数据核算应为 {fact_text}（{period}），存在矛盾",
                "correction": f"{claim.label}应为 {fact_text}（{period}），而非 {claim_text}"}

    @staticmethod
    def _display(kind: str, value: float) -> str:
        if kind == "amount":
            return fmt_amount(value)
        if kind == "percent":
            return fmt_pct(value, signed=False)
        return f"{value:.2f}"

    # ------------------------------------------------------------------
    def _extract_claims(self, text: str) -> List[Claim]:
        claims: List[Claim] = []
        for spec in METRICS:
            if spec.yoy_anchor:
                found = self._find_yoy(text, spec)
            else:
                found = self._find_standard(text, spec)
            claims.extend(found)
        return claims

    def _find_standard(self, text: str, spec: MetricSpec) -> List[Claim]:
        out: List[Claim] = []
        for alias in spec.aliases:
            for match in re.finditer(re.escape(alias), text, flags=re.IGNORECASE):
                window = text[match.end(): match.end() + 24]
                value, raw = self._read_value(window, spec.kind)
                if value is not None:
                    out.append(Claim(spec.key, spec.label, spec.kind, value, raw))
                    break  # 该别名命中即可
            if out:
                break
        return out

    def _find_yoy(self, text: str, spec: MetricSpec) -> List[Claim]:
        anchors = _YOY_ANCHORS.get(spec.key, spec.aliases)
        for anchor in anchors:
            for match in re.finditer(re.escape(anchor), text, flags=re.IGNORECASE):
                window = text[match.end(): match.end() + 44]
                # 在窗口内寻找 同比/较上年 ... （增长/下降）... 数字%
                yoy_hit = re.search(
                    r"(?:同比|较上年同期|较去年同期|同比上年)?[^0-9%]{0,10}"
                    r"(增长|增加|上升|提升|下降|下滑|减少|降低)?\s*" + _NUM + r"\s*(?:个百分点|%)",
                    window,
                )
                if not yoy_hit:
                    continue
                neg = yoy_hit.group(1) in _NEG_WORDS
                value = float(yoy_hit.group(2)) * (-1 if neg else 1)
                return [Claim(spec.key, spec.label, "percent", value, yoy_hit.group(0).strip())]
        return []

    @staticmethod
    def _read_value(window: str, kind: str) -> Tuple[Optional[float], str]:
        if kind == "amount":
            m = _AMOUNT_RE.search(window)
            if not m:
                return None, ""
            unit = m.group(2)
            mult = {"万亿": 1e12, "亿": _YI, "万": _WAN}[unit]
            return float(m.group(1)) * mult, m.group(0)
        if kind == "percent":
            m = _PCT_RE.search(window)
            if not m:
                return None, ""
            return float(m.group(1)), m.group(0)
        # price：跳过 4 位年份
        for m in _PRICE_RE.finditer(window):
            token = m.group(0)
            if _YEAR_RE.search(window[max(0, m.start() - 2): m.end() + 2]):
                continue
            return float(m.group(1)), token
        return None, ""
