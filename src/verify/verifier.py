"""交叉校验器（反幻觉第二道防线——输出侧逐条核对）。

流程：
1. 从大模型生成文本中抽取“数值声明”（营收、净利润、同比、毛利率、ROE、EPS、股价…）；
2. 与 :class:`~src.verify.facts.FactBase` 中由真实数据计算出的事实按容差比对；
3. 输出每条声明的状态：``supported``（一致）/ ``mismatch``（与事实矛盾，疑似幻觉）/
   ``unverifiable``（事实库未覆盖，无法证实）；
4. 生成纠错清单，可回填给大模型进行一次“基于核实值”的重写。

该校验是确定性的数值比对，不依赖模型自觉，从而真正拦截“一本正经地编数字”。

修复要点（v4）：
- 同比保留正负号：``同比下降8.5%`` / ``同比下滑8.5%`` / ``同比 -8.5%`` 均为 ``-8.5``；
- 同比词必须紧邻数字，且不得跨指标取数（禁止把毛利率当成营收同比）；
- 同一指标**收集全部出现位置**，任一处矛盾都会被拦截；
- 支持千分位数字 ``1,234.56``；
- 容差与 README 对齐（金额 1%、百分点 0.1），并处理浮点边界；
- 幻觉率分母 = 全部抽到的数值出现次数；``unverifiable`` 占比过高时下调可信度；
- price 类抽取要求合理的单位/上下文，避免把 ``20.3%`` 当股价、把股价当 PE。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..common.logger import get_logger
from .facts import FactBase, fmt_amount, fmt_pct

logger = get_logger("verify.cross")

_YI, _WAN = 1e8, 1e4
# 容差（与 README:190「容差 1%」一致）
REL_TOL = 0.01          # 金额 / 价格相对容差 1%
PCT_TOL = 0.1           # 百分点绝对容差 0.1
PRICE_TOL = 0.01
_FLOAT_EPS = 1e-9       # 浮点边界保护（如 0.1 实际为 0.10000000000000142）

# 数值：支持千分位与正负号
_NUMPAT = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
# 指标名与数值之间允许出现的“无歧义”连接词/分隔符
_CONN = (r"(?:\s|[:：,，、;；()（）]|"
         r"达到|收于|降至|升至|录得|实现|实际为|实际|约为|大约|共计|合计|"
         r"为|是|约|报|达|至)*")
_AMOUNT_RE = re.compile(_CONN + r"(" + _NUMPAT + r")\s*(万亿|亿|万)\s*元?")
_PCT_RE = re.compile(_CONN + r"(" + _NUMPAT + r")\s*(?:个百分点|%|％)")
# 价格类：必须紧邻指标名；单位可有可无（PE/PB 无单位），但必须排除百分号与年份
_PRICE_RE = re.compile(_CONN + r"(" + _NUMPAT + r")\s*(元|港元|美元|倍)?")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")

# 同比词与数字之间只允许方向词/连接词，不得跨越数字、百分号或标点
_YOY_WORD = r"(?:同比|较上年同期|较去年同期|比上年同期|同比上年)"
_YOY_RE = re.compile(
    _YOY_WORD
    + r"([^0-9%％\n，。；、]{0,8}?)"
    + r"(" + _NUMPAT + r")\s*(?:个百分点|%|％)"
)
# 锚点与同比词之间的最大跨度
_YOY_WINDOW = 40
# 锚点与同比词之间禁止跨越的字符（允许逗号：`营收152.3亿元，同比增长12.5%` 是合法写法）
_YOY_BLOCK_CHARS = "。；%％\n"


@dataclass
class MetricSpec:
    key: str
    label: str
    kind: str                      # amount / percent / price
    aliases: List[str] = field(default_factory=list)
    yoy_anchor: bool = False       # 同比类：在关联指标窗口内找“同比…%”
    unit_required: bool = False    # price 类是否必须带单位（股价/EPS 需要，PE/PB 不需要）


# 指标词典：别名按“先长后短、先具体后宽泛”排列
METRICS: List[MetricSpec] = [
    MetricSpec("revenue", "营业总收入", "amount",
               ["营业总收入", "营业收入", "营收"]),
    MetricSpec("net_profit", "归母净利润", "amount",
               ["归属于上市公司股东的净利润", "归母净利润", "净利润"]),
    MetricSpec("net_profit_deducted", "扣非归母净利润", "amount",
               ["扣除非经常性损益后的净利润", "扣非归母净利润", "扣非净利润"]),
    MetricSpec("revenue_yoy", "营业总收入同比增速", "percent",
               ["营业总收入同比", "营业收入同比", "营收同比", "营收增速",
                "营业总收入增长率", "营业收入增长率", "营收增长"], yoy_anchor=True),
    MetricSpec("net_profit_yoy", "归母净利润同比增速", "percent",
               ["归母净利润同比", "净利润同比", "净利润增速",
                "净利润增长"], yoy_anchor=True),
    MetricSpec("net_profit_deducted_yoy", "扣非净利润同比增速", "percent",
               ["扣非净利润同比", "扣非归母净利润同比"], yoy_anchor=True),
    MetricSpec("gross_margin", "销售毛利率", "percent", ["销售毛利率", "毛利率"]),
    MetricSpec("net_margin", "销售净利率", "percent", ["销售净利率", "净利率"]),
    MetricSpec("roe", "净资产收益率", "percent",
               ["加权平均净资产收益率", "净资产收益率", "ROE"]),
    MetricSpec("eps", "基本每股收益", "price",
               ["基本每股收益", "每股收益", "EPS"], unit_required=True),
    MetricSpec("latest_close", "最新收盘价", "price",
               ["最新收盘价", "收盘价", "股价"], unit_required=True),
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

# 同比指标关联的正指标锚点（用于定位“指标 … 同比 …%”）
# 注意：不含裸“净利”，避免命中“净利率”
_YOY_ANCHORS = {
    "revenue_yoy": ["营业总收入", "营业收入", "营收"],
    "net_profit_yoy": ["归属于上市公司股东的净利润", "归母净利润", "净利润"],
    "net_profit_deducted_yoy": ["扣除非经常性损益后的净利润", "扣非归母净利润", "扣非净利润"],
}
_NEG_WORDS = ("下降", "下滑", "减少", "降低", "回落", "收窄", "转负", "跌幅", "负增长")


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
    def unverifiable_ratio(self) -> float:
        """无法核实项占比：越高说明事实库覆盖越差，不应因此“显得可信”。"""
        if not self.claims:
            return 0.0
        return round(self.n_unverifiable / len(self.claims), 3)

    @property
    def hallucination_rate(self) -> float:
        # 分母 = 全部抽到的数值出现次数（含 unverifiable），不再是去重后的条数
        if not self.claims:
            return 0.0
        return round(self.n_mismatch / len(self.claims), 3)

    @property
    def passed(self) -> bool:
        return self.n_mismatch == 0

    @property
    def confidence_level(self) -> str:
        """可信度等级（A=高可信 / B=中可信 / C=低可信 / D=不可信）。

        原始阈值与 README 一致（<5% / 5-15% / 15-30% / >30%）。
        额外约束：``unverifiable`` 占比不得抬高可信度——无法核实的条目过多时下调等级，
        避免“事实库覆盖不到的数字越多越可信”。
        """
        rate = self.hallucination_rate
        if rate < 0.05:
            level = 0
        elif rate < 0.15:
            level = 1
        elif rate < 0.30:
            level = 2
        else:
            level = 3

        ur = self.unverifiable_ratio
        if ur > 0.8:
            level = min(level + 2, 3)
        elif ur > 0.5:
            level = min(level + 1, 3)
        names = ["A（高可信）", "B（中可信）", "C（低可信）", "D（不可信）"]
        return names[level]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "confidence_level": self.confidence_level,
            "total_claims": len(self.claims),
            "supported": self.n_supported,
            "mismatch": self.n_mismatch,
            "unverifiable": self.n_unverifiable,
            "unverifiable_ratio": self.unverifiable_ratio,
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
        for claim in claims:
            fact = fact_base.get(claim.metric)
            report.claims.append(self._judge(claim, fact))
        # 同一指标多处出现时全部保留（任一矛盾都要被发现）
        report.claims.sort(key=lambda x: (x["metric"], x.get("claimed_raw", "")))
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
            # 加浮点保护：0.1 在二进制下可能略大于 0.1
            ok = diff <= self.pct_tol + _FLOAT_EPS
        else:
            denom = abs(fact.value) or 1e-9
            diff = abs(claim.claimed - fact.value)
            ok = diff / denom <= self.rel_tol + _FLOAT_EPS

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
        spans = self._alias_spans(text)
        claims: List[Claim] = []
        for spec in METRICS:
            if spec.yoy_anchor:
                found = self._find_yoy(text, spec, spans)
            else:
                found = self._find_standard(text, spec, spans)
            claims.extend(found)
        return claims

    # -- 别名跨度（用于判断是否跨指标）--------------------------------
    def _alias_spans(self, text: str) -> List[Tuple[int, int, str]]:
        spans: List[Tuple[int, int, str]] = []
        for spec in METRICS:
            for alias in spec.aliases:
                for m in re.finditer(re.escape(alias), text, flags=re.IGNORECASE):
                    spans.append((m.start(), m.end(), spec.key))
        for key, anchors in _YOY_ANCHORS.items():
            for alias in anchors:
                for m in re.finditer(re.escape(alias), text, flags=re.IGNORECASE):
                    spans.append((m.start(), m.end(), key))
        return spans

    @staticmethod
    def _overlapped_by_other(start: int, end: int, key: str,
                             spans: List[Tuple[int, int, str]]) -> bool:
        """当前位置是否落在“更长的其它指标别名”内部（如 `净利润` ⊂ `扣非…净利润`）。"""
        length = end - start
        for (s, e, k) in spans:
            if k == key:
                continue
            if e - s <= length:
                continue
            if s < end and e > start:
                return True
        return False

    @staticmethod
    def _span_has_other_alias(start: int, end: int, key: str,
                              spans: List[Tuple[int, int, str]]) -> bool:
        for (s, e, k) in spans:
            if k == key:
                continue
            if s < end and e > start:
                return True
        return False

    # -- 普通指标：收集全部出现位置 -----------------------------------
    def _find_standard(self, text: str, spec: MetricSpec,
                       spans: List[Tuple[int, int, str]]) -> List[Claim]:
        out: Dict[int, Claim] = {}
        for alias in spec.aliases:
            for match in re.finditer(re.escape(alias), text, flags=re.IGNORECASE):
                start, end = match.start(), match.end()
                # 落在更长的其它指标别名内部（如“净利率”里的“净利”）→ 跳过
                if self._overlapped_by_other(start, end, spec.key, spans):
                    continue
                window = text[end: end + 24]
                value, raw, offset = self._read_value(window, spec.kind, spec.unit_required)
                if value is None:
                    continue
                # 以数值在原文中的位置去重，避免长短别名重复抽同一条
                out.setdefault(end + offset, Claim(spec.key, spec.label, spec.kind, value, raw))
        return list(out.values())

    # -- 同比指标：锚点 + 紧邻数字 + 禁止跨指标 ------------------------
    def _find_yoy(self, text: str, spec: MetricSpec,
                  spans: List[Tuple[int, int, str]]) -> List[Claim]:
        anchors = _YOY_ANCHORS.get(spec.key, spec.aliases)
        anchor_positions: List[Tuple[int, int]] = []
        for alias in anchors:
            for match in re.finditer(re.escape(alias), text, flags=re.IGNORECASE):
                start, end = match.start(), match.end()
                if self._overlapped_by_other(start, end, spec.key, spans):
                    continue
                anchor_positions.append((start, end))
        if not anchor_positions:
            return []

        out: Dict[int, Claim] = {}
        for ym in _YOY_RE.finditer(text):
            yoy_start = ym.start()
            # 从最近的锚点往前找，第一个“不跨指标、不跨句/百分号”的锚点被采用
            chosen = None
            for (start, end) in sorted(anchor_positions, key=lambda x: -x[1]):
                if end > yoy_start:
                    continue
                if yoy_start - end > _YOY_WINDOW:
                    continue
                gap = text[end:yoy_start]
                if any(ch in gap for ch in _YOY_BLOCK_CHARS):
                    continue
                if self._span_has_other_alias(end, yoy_start, spec.key, spans):
                    continue
                chosen = (start, end)
                break
            if chosen is None:
                continue

            filler = ym.group(1) or ""
            num = self._to_float(ym.group(2))
            if num is None:
                continue
            # 方向词决定符号；显式负号保留
            if any(w in filler for w in _NEG_WORDS) and num > 0:
                num = -num
            pos = ym.start(2)
            raw = text[ym.start():ym.end()].strip()
            out.setdefault(pos, Claim(spec.key, spec.label, "percent", num, raw))
        return list(out.values())

    # -- 数值解析 -----------------------------------------------------
    @staticmethod
    def _to_float(raw: str) -> Optional[float]:
        try:
            return float(str(raw).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    @classmethod
    def _read_value(cls, window: str, kind: str,
                    unit_required: bool = False) -> Tuple[Optional[float], str, int]:
        """从窗口开头读取数值，返回 (值, 原文, 数值在窗口内的偏移)。"""
        if kind == "amount":
            m = _AMOUNT_RE.match(window)
            if not m:
                return None, "", 0
            value = cls._to_float(m.group(1))
            if value is None:
                return None, "", 0
            mult = {"万亿": 1e12, "亿": _YI, "万": _WAN}[m.group(2)]
            return value * mult, m.group(0), m.start(1)
        if kind == "percent":
            m = _PCT_RE.match(window)
            if not m:
                return None, "", 0
            value = cls._to_float(m.group(1))
            if value is None:
                return None, "", 0
            return value, m.group(0), m.start(1)

        # price：必须紧邻指标名；排除百分号、年份；必要时要求单位
        m = _PRICE_RE.match(window)
        if not m:
            return None, "", 0
        tail = window[m.end(): m.end() + 4]
        if tail.startswith("%") or tail.startswith("％") or tail.startswith("个百分点"):
            return None, "", 0
        num = cls._to_float(m.group(1))
        if num is None:
            return None, "", 0
        raw_num = str(m.group(1)).replace(",", "").lstrip("+-")
        if _YEAR_RE.fullmatch(raw_num) and window[m.end(): m.end() + 1] == "年":
            return None, "", 0
        unit = m.group(2)
        if unit_required and not unit:
            return None, "", 0
        return num, m.group(0), m.start(1)
