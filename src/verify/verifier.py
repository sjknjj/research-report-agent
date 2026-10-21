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
from .numbers import (
    KIND_AMOUNT,
    KIND_BARE,
    KIND_MULTIPLE,
    KIND_PERCENT,
    KIND_PRICE,
    extract_numbers,
    format_number,
    match_cn_at,
)

logger = get_logger("verify.cross")

_YI, _WAN = 1e8, 1e4
# 容差（T1：按指标重要性分级，核心指标收紧到 0.5%）
REL_TOL = 0.01          # 一般指标的金额/价格相对容差 1%
REL_TOL_CORE = 0.005    # 核心指标（营收/净利/EPS/ROE/毛利率…）收紧到 0.5%
PCT_TOL = 0.1           # 一般指标的百分点绝对容差 0.1
PCT_TOL_CORE = 0.06     # 核心指标的百分点绝对容差（保留 1 位小数的舍入空间）
PRICE_TOL = 0.01
_FLOAT_EPS = 1e-9       # 浮点边界保护（如 0.1 实际为 0.10000000000000142）

# 核心指标：金额/比率直接决定结论，容差收紧
CORE_METRICS = frozenset({
    "revenue", "net_profit", "net_profit_deducted", "eps", "roe",
    "gross_margin", "net_margin", "revenue_yoy", "net_profit_yoy",
    "net_profit_deducted_yoy", "revenue_qoq", "net_profit_qoq",
})

# T1：结论阈值——覆盖率过低/无来源过多时不允许显示"通过"
COVERAGE_MIN = 0.60        # 可核实覆盖率下限
UNSOURCED_MAX = 0.30       # 无来源数字占比上限
UNVERIFIABLE_MAX = 0.50    # 无法核实占比上限


def tol_for(metric: str, kind: str) -> float:
    """按指标重要性返回容差。"""
    core = metric in CORE_METRICS
    if kind == "percent":
        return PCT_TOL_CORE if core else PCT_TOL
    return REL_TOL_CORE if core else REL_TOL

# 数值：支持千分位与正负号
_NUMPAT = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
# 指标名与数值之间允许出现的“无歧义”连接词/分隔符
# 注意：必须容纳 Markdown 的 **加粗** / `代码` / _斜体_ 标记。
# 模型写报告时几乎总是 `销售毛利率 **29.99%**`，标记符号夹在中间，
# 旧版连接符里没有 `*`，于是这类最规范的写法反而一个都抽不到。
_CONN = (r"(?:\s|[:：,，、;；()（）*_`~]|"
         r"达到|收于|降至|升至|录得|实现|实际为|实际|约为|大约|共计|合计|"
         r"为|是|约|报|达|至)*")
_AMOUNT_RE = re.compile(_CONN + r"(" + _NUMPAT + r")\s*(万亿|亿|万)\s*元?")
_PCT_RE = re.compile(_CONN + r"(" + _NUMPAT + r")\s*(?:个百分点|%|％)")
# 价格类：必须紧邻指标名；单位可有可无（PE/PB 无单位），但必须排除百分号与年份
_PRICE_RE = re.compile(_CONN + r"(" + _NUMPAT + r")\s*(元|港元|美元|倍)?")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
# 连接词前缀（用于中文数字兜底时跳过 "为/达到/约" 等）
_CONN_RE = re.compile(_CONN)

# 锚点与增速词之间的最大跨度
_YOY_WINDOW = 40
# 锚点与增速词之间禁止跨越的字符（允许逗号：`营收152.3亿元，同比增长12.5%` 是合法写法）
_YOY_BLOCK_CHARS = "。；%％\n"
_GROWTH_RE_CACHE: Dict[Tuple[str, ...], re.Pattern] = {}


def _growth_re(words: Tuple[str, ...]) -> re.Pattern:
    """按增速词（同比/环比…）构造正则：增速词 + 可选方向词 + 数字 + %。"""
    key = tuple(words)
    rx = _GROWTH_RE_CACHE.get(key)
    if rx is None:
        alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
        rx = re.compile(
            r"(?:" + alt + r")"
            + r"([^0-9%％\n，。；、]{0,8}?)"
            + r"(" + _NUMPAT + r")\s*(?:个百分点|%|％)"
        )
        _GROWTH_RE_CACHE[key] = rx
    return rx


@dataclass
class MetricSpec:
    key: str
    label: str
    kind: str                      # amount / percent / price
    aliases: List[str] = field(default_factory=list)
    yoy_anchor: bool = False       # 增速类：在关联指标窗口内找"同比/环比…%"
    unit_required: bool = False    # price 类是否必须带单位（股价/EPS 需要，PE/PB 不需要）
    growth_words: Tuple[str, ...] = (
        "同比", "较上年同期", "较去年同期", "比上年同期", "同比上年",
    )


# 指标词典：别名按"先长后短、先具体后宽泛"排列
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
               ["归母净利润同比", "净利润同比", "净利润增速", "净利同比",
                "归母净利同比", "净利增速", "净利润增长"], yoy_anchor=True),
    MetricSpec("net_profit_deducted_yoy", "扣非净利润同比增速", "percent",
               ["扣非净利润同比", "扣非归母净利润同比"], yoy_anchor=True),
    # U2：环比（较上期）——与同比分开，避免把"环比"当成"同比"比对
    MetricSpec("revenue_qoq", "营业总收入较上期增速", "percent",
               ["营业总收入环比", "营业收入环比", "营收环比", "营业总收入较上期",
                "营收较上期"], yoy_anchor=True,
               growth_words=("环比", "较上期", "较上一期", "比上期")),
    MetricSpec("net_profit_qoq", "归母净利润较上期增速", "percent",
               ["归母净利润环比", "净利润环比", "净利环比", "净利润较上期",
                "归母净利润较上期"], yoy_anchor=True,
               growth_words=("环比", "较上期", "较上一期", "比上期")),
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
    # 均线：事实包里一直有（"20 日均线：86.89"），词典里却没有，
    # 报告引用它就成了"无来源"——把词典补上，模型写对的东西才不会被冤枉
    MetricSpec("ma5", "5日均线", "price",
               ["5日均线", "5 日均线", "MA5", "五日均线"]),
    MetricSpec("ma20", "20日均线", "price",
               ["20日均线", "20 日均线", "MA20", "二十日均线"]),
    # 这三项事实库里本来就有，此前不在词典里，导致报告引用它们时被误判成"无来源"
    MetricSpec("period_high", "区间最高价", "price",
               ["区间最高价", "区间最高"], unit_required=True),
    MetricSpec("period_low", "区间最低价", "price",
               ["区间最低价", "区间最低"], unit_required=True),
    MetricSpec("avg_turnover", "平均换手率", "percent", ["平均换手率"]),
    MetricSpec("annual_vol", "年化波动率", "percent", ["年化波动率", "波动率"]),
    MetricSpec("pe_ratio", "市盈率(PE)", "price",
               ["市盈率(PE-TTM)", "市盈率", "PE(TTM)", "PE"]),
    MetricSpec("pb_ratio", "市净率(PB)", "price",
               ["市净率(PB)", "市净率", "PB"]),
    MetricSpec("dividend_yield", "股息率", "percent", ["股息率"]),
    MetricSpec("main_net_inflow", "主力净流入", "amount",
               ["主力净流入净额", "主力净流入", "主力资金净流入"]),
]

# 同比/环比指标关联的正指标锚点（用于定位"指标 … 同比 …%"）
# 注意：不含裸"净利"，避免命中"净利率"
_YOY_ANCHORS = {
    "revenue_yoy": ["营业总收入", "营业收入", "营收"],
    "net_profit_yoy": ["归属于上市公司股东的净利润", "归母净利润", "净利润"],
    "net_profit_deducted_yoy": ["扣除非经常性损益后的净利润", "扣非归母净利润", "扣非净利润"],
    "revenue_qoq": ["营业总收入", "营业收入", "营收"],
    "net_profit_qoq": ["归属于上市公司股东的净利润", "归母净利润", "净利润"],
}
_NEG_WORDS = ("下降", "下滑", "减少", "降低", "回落", "收窄", "转负", "跌幅", "负增长")


@dataclass
class Claim:
    metric: str
    label: str
    kind: str
    claimed: float
    raw: str
    pos: int = -1          # 数值字面量在原文中的起始下标（用于与全量抽取对齐）
    context: str = ""      # 数值附近上下文（用于分期归因）
    period: str = ""       # 表格解析出的报告期（优先于从 context 猜）


@dataclass
class VerificationReport:
    claims: List[Dict[str, Any]] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    corrections: List[str] = field(default_factory=list)
    # U1：无法归因到任何指标、也不与事实库任何数值吻合的数字
    unsourced: List[Dict[str, Any]] = field(default_factory=list)
    # U1b：数值与事实库**确实一致**、只是文中没写指标名的数字
    #      （典型来源：近五期趋势表格里那一列列数字）
    #      它们不该算"无来源"——那样会把「有据可查但没点名」误报成「疑似编造」。
    implicit: List[Dict[str, Any]] = field(default_factory=list)
    # U1：被忽略的噪声数字（年份/代码/无单位计数），不计入覆盖率分母
    ignored_numbers: int = 0
    # U3：同一指标在文中出现多次但数值互相矛盾
    contradictions: List[Dict[str, Any]] = field(default_factory=list)

    # ---- 计数 -----------------------------------------------------------
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
    def total_numbers(self) -> int:
        """报告中需要核对的数字总数（已归因 claims + 未归因 unsourced + implicit）。"""
        return len(self.claims) + len(self.unsourced) + len(self.implicit)

    # ---- 比率 -----------------------------------------------------------
    @property
    def unverifiable_ratio(self) -> float:
        """无法核实项占比：越高说明事实库覆盖越差，不应因此"显得可信"。"""
        if not self.total_numbers:
            return 0.0
        return round(self.n_unverifiable / self.total_numbers, 3)

    @property
    def unsourced_ratio(self) -> float:
        """无来源归因的数字占比（U1）。

        ``implicit`` 不计入这里：它们的数值在事实库里对得上，
        只是文中没点名指标，属于"可核实但表述含糊"，不是"疑似编造"。
        """
        if not self.total_numbers:
            return 0.0
        return round(len(self.unsourced) / self.total_numbers, 3)

    @property
    def coverage(self) -> float:
        """可核实覆盖率 =（一致数字 + 隐式可核实数字）/ 全部数字。"""
        if not self.total_numbers:
            return 0.0
        return round((self.n_supported + len(self.implicit)) / self.total_numbers, 3)

    @property
    def hallucination_rate(self) -> float:
        # 分母 = 已归因的数值出现次数（含 unverifiable）
        if not self.claims:
            return 0.0
        return round(self.n_mismatch / len(self.claims), 3)

    @property
    def has_mismatch(self) -> bool:
        """是否存在与事实库矛盾的数字（重写循环应以此为准，而不是严格版 passed）。"""
        return self.n_mismatch > 0

    # ---- T1：三态结论 ---------------------------------------------------
    # 旧版 passed 只看"有没有矛盾"，于是"数字全是无来源"的报告也会显示"通过"，
    # 这是最危险的假阳性安全感。现在结论分三态：通过 / 通过（需人工复核）/ 未通过。
    @property
    def verdict(self) -> str:
        """``pass`` / ``review`` / ``fail``。"""
        if self.has_mismatch:
            return "fail"
        if self.total_numbers == 0:
            # 一个财务数字都没抽到：不能算"通过"，只能说没东西可核
            return "review"
        if self.coverage < COVERAGE_MIN:
            return "review"
        if self.unsourced_ratio > UNSOURCED_MAX:
            return "review"
        if self.unverifiable_ratio > UNVERIFIABLE_MAX:
            return "review"
        if self.contradictions:
            return "review"
        return "pass"

    @property
    def verdict_text(self) -> str:
        return {"pass": "通过", "review": "通过但有保留（需人工复核）",
                "fail": "未通过"}[self.verdict]

    @property
    def verdict_reasons(self) -> List[str]:
        """为什么不是"通过"（含 fail 与 review 的原因）。"""
        reasons: List[str] = []
        if self.has_mismatch:
            reasons.append(f"存在 {self.n_mismatch} 处与事实库矛盾的数字")
        if self.total_numbers == 0:
            reasons.append("未能从正文中抽取到任何财务数字，无法核实")
            return reasons
        if self.coverage < COVERAGE_MIN:
            reasons.append(
                f"可核实覆盖率仅 {self.coverage:.0%}（低于 {COVERAGE_MIN:.0%}）"
            )
        if self.unsourced_ratio > UNSOURCED_MAX:
            reasons.append(
                f"无来源数字占比 {self.unsourced_ratio:.0%}（高于 {UNSOURCED_MAX:.0%}），"
                f"共 {len(self.unsourced)} 个数字无法归因"
            )
        if self.unverifiable_ratio > UNVERIFIABLE_MAX:
            reasons.append(
                f"事实库无法核实的数字占比 {self.unverifiable_ratio:.0%}"
                f"（高于 {UNVERIFIABLE_MAX:.0%}）"
            )
        if self.contradictions:
            reasons.append(f"报告内部存在 {len(self.contradictions)} 处自相矛盾")
        return reasons

    @property
    def passed(self) -> bool:
        """严格通过：无矛盾 **且** 覆盖率达标、无来源/无法核实比例不过高。"""
        return self.verdict == "pass"

    @property
    def passed_lenient(self) -> bool:
        """宽松通过（仅表示"没有与事实库矛盾"），兼容旧语义。"""
        return self.n_mismatch == 0

    # ---- 可信度（U4：按覆盖率/无来源占比校准）---------------------------
    def _downgrades(self) -> Tuple[int, List[str]]:
        steps = 0
        reasons: List[str] = []

        ur = self.unverifiable_ratio
        if ur > 0.8:
            steps += 2
            reasons.append(f"事实库无法核实的数字占比 {ur:.0%}（>80%），降 2 级")
        elif ur > 0.5:
            steps += 1
            reasons.append(f"事实库无法核实的数字占比 {ur:.0%}（>50%），降 1 级")

        us = self.unsourced_ratio
        if us > 0.5:
            steps += 2
            reasons.append(f"无来源数字占比 {us:.0%}（>50%），降 2 级")
        elif us > 0.25:
            steps += 1
            reasons.append(f"无来源数字占比 {us:.0%}（>25%），降 1 级")

        cov = self.coverage
        if self.total_numbers and cov < 0.3:
            steps += 2
            reasons.append(f"可核实覆盖率仅 {cov:.0%}（<30%），降 2 级")
        elif self.total_numbers and cov < 0.6:
            steps += 1
            reasons.append(f"可核实覆盖率仅 {cov:.0%}（<60%），降 1 级")

        if self.contradictions:
            steps += 1
            reasons.append(f"报告内部存在 {len(self.contradictions)} 处自相矛盾，降 1 级")
        return steps, reasons

    @property
    def confidence_reasons(self) -> List[str]:
        return self._downgrades()[1]

    @property
    def confidence_level(self) -> str:
        """可信度等级（A=高可信 / B=中可信 / C=低可信 / D=不可信）。

        基础阈值与 README 一致（<5% / 5-15% / 15-30% / >30%）。
        U4 新增校准：无法核实/无来源数字过多、覆盖率过低、内部自相矛盾时下调等级，
        避免"没被检查的数字越多反而越可信"。
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

        steps, _ = self._downgrades()
        level = min(level + steps, 3)
        names = ["A（高可信）", "B（中可信）", "C（低可信）", "D（不可信）"]
        return names[level]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "passed_lenient": self.passed_lenient,
            "verdict": self.verdict,
            "verdict_text": self.verdict_text,
            "verdict_reasons": self.verdict_reasons,
            "confidence_level": self.confidence_level,
            "confidence_reasons": self.confidence_reasons,
            "total_claims": len(self.claims),
            "total_numbers": self.total_numbers,
            "supported": self.n_supported,
            "mismatch": self.n_mismatch,
            "unverifiable": self.n_unverifiable,
            "unsourced": len(self.unsourced),
            "implicit": len(self.implicit),
            "ignored_numbers": self.ignored_numbers,
            "unverifiable_ratio": self.unverifiable_ratio,
            "unsourced_ratio": self.unsourced_ratio,
            "coverage": self.coverage,
            "hallucination_rate": self.hallucination_rate,
            "claims": self.claims,
            "unsourced_items": self.unsourced[:20],
            "implicit_items": self.implicit[:20],
            "contradictions": self.contradictions,
            "issues": self.issues,
            "corrections": self.corrections,
        }

    def render_text(self) -> str:
        lines = [
            f"交叉校验结论：{self.verdict_text}｜"
            f"可信度：{self.confidence_level}｜"
            f"共核对 {self.total_numbers} 个数字：一致 {self.n_supported}、"
            f"矛盾 {self.n_mismatch}、无法核实 {self.n_unverifiable}、"
            f"未指明指标但已核实 {len(self.implicit)}、"
            f"无来源 {len(self.unsourced)}｜"
            f"可核实覆盖率 {self.coverage:.0%}｜"
            f"疑似幻觉率 {self.hallucination_rate:.1%}",
        ]
        if self.verdict_reasons:
            lines.append("结论保留意见：")
            lines.extend(f"  - {r}" for r in self.verdict_reasons)
        for c in self.claims:
            mark = {"supported": "✓", "mismatch": "✗", "unverifiable": "?"}[c["status"]]
            lines.append(f"  [{mark}] {c['label']}：{c['note']}")
        if self.implicit:
            lines.append(f"未指明指标、但数值与事实库一致（{len(self.implicit)} 个）：")
            for item in self.implicit[:10]:
                lines.append(f"  [≈] {item['raw']}：{item.get('hint', item['note'])}")
        if self.unsourced:
            lines.append(f"无来源数字（{len(self.unsourced)} 个，需人工复核）：")
            for item in self.unsourced[:10]:
                lines.append(f"  [!] {item['raw']}：{item['note']}")
        if self.contradictions:
            lines.append("报告内部自相矛盾：")
            for item in self.contradictions:
                lines.append(f"  [≠] {item['label']}：文中出现 {item['values']}")
        if self.confidence_reasons:
            lines.append("可信度下调原因：")
            lines.extend(f"  - {r}" for r in self.confidence_reasons)
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
        # 表格里的数字（"近五期趋势"那种）纯文本正则抽不到，单独按表头绑定指标
        claims.extend(self._table_claims(text))
        report = VerificationReport()
        claimed_positions: List[int] = []
        seen_positions = set()
        for claim in claims:
            # 文本正则和表格解析可能命中同一个位置，只算一次
            if claim.pos >= 0:
                if claim.pos in seen_positions:
                    continue
                seen_positions.add(claim.pos)
            fact = self._fact_for(claim, fact_base)
            report.claims.append(self._judge(claim, fact))
            if claim.pos >= 0:
                claimed_positions.append(claim.pos)
        # 同一指标多处出现时全部保留（任一矛盾都要被发现）
        report.claims.sort(key=lambda x: (x["metric"], x.get("claimed_raw", "")))
        for item in report.claims:
            if item["status"] == "mismatch":
                report.issues.append(f"{item['label']}：{item['note']}")
                report.corrections.append(item["correction"])

        # U1：与词典无关的"全量数字归因"——把没被检查的数字明确标为"无来源"
        self._classify_unattributed(text, fact_base, claimed_positions, report)
        # U3：报告内部自相矛盾
        report.contradictions = self._find_contradictions(report.claims)
        return report

    # -- 表格按列头归因 ---------------------------------------------------
    #: 表头里的单位写法 → 金额倍数
    _UNIT_SCALE = {"万亿": 1e12, "亿": 1e8, "万": 1e4, "元": 1.0}
    _CELL_NUM_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*(%|％)?")
    _PERIOD_CELL_RE = re.compile(
        r"^\s*((?:19|20)\d{2})\s*[-/年]?\s*(\d{1,2})?\s*[-/月]?\s*(\d{1,2})?\s*日?\s*$"
    )
    _UNIT_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")

    def _alias_index(self) -> List[Tuple[str, MetricSpec]]:
        """(别名, 指标) 按长度从长到短排，保证"营收同比"先于"营收"命中。"""
        pairs: List[Tuple[str, MetricSpec]] = []
        for spec in METRICS:
            for alias in spec.aliases:
                pairs.append((alias, spec))
            pairs.append((spec.label, spec))
        pairs.sort(key=lambda x: len(x[0]), reverse=True)
        return pairs

    def _header_metric(self, cell: str):
        """表头单元格 → (MetricSpec, 金额倍数 或 None)。

        ``None`` 表示表头没写单位，交给表格上方的「单位：亿元」提示兜底。
        """
        text = str(cell).strip()
        if not text:
            return None, None
        inner = " ".join(self._UNIT_PAREN_RE.findall(text))
        scale = None
        for unit, factor in self._UNIT_SCALE.items():
            if unit in inner or unit in text:
                scale = factor
                break
        core = self._UNIT_PAREN_RE.sub("", text).strip()
        for alias, spec in self._alias_index():
            if alias and alias in core:
                return spec, scale
        return None, scale

    #: 表格上方的单位提示，如「**近五期趋势（单位：亿元）**」
    _BLOCK_UNIT_RE = re.compile(r"单位\s*[:：]?\s*(万亿|亿元|万元|千元|元)")

    @classmethod
    def _block_unit_scale(cls, lines: List[str], header_idx: int) -> float:
        """从表格上方 2 行内找「单位：亿元」这类提示。

        报告的表格常常把单位写在标题行而不是表头里：
        ``**近五期趋势（单位：亿元）**`` 下面直接就是 ``| 报告期 | 营业总收入 |``。
        不读这一行，13.64 会被当成 13.64 元，整张表都判成"与事实不符"。
        """
        for back in (1, 2):
            idx = header_idx - back
            if idx < 0:
                break
            m = cls._BLOCK_UNIT_RE.search(lines[idx])
            if m:
                unit = m.group(1)
                if unit == "万亿":
                    return 1e12
                if unit == "亿元":
                    return 1e8
                if unit == "万元":
                    return 1e4
                if unit == "千元":
                    return 1e3
                return 1.0
        return 1.0

    @classmethod
    def _cell_period(cls, cell: str) -> str:
        m = cls._PERIOD_CELL_RE.match(str(cell).strip())
        if not m:
            return ""
        year, month, day = m.group(1), m.group(2), m.group(3)
        if month and day:
            return f"{year}-{int(month):02d}-{int(day):02d}"
        if month:
            return f"{year}-{int(month):02d}"
        return year

    def _table_claims(self, text: str) -> List[Claim]:
        """从 markdown 表格按「表头→指标、首列→报告期」抽取数值声明。

        报告里的「近五期趋势」是这种结构::

            | 报告期 | 营业总收入(亿元) | 营收同比 | ... |
            | 2026-09-30 | 13.64 | +70.71% | ... |

        没有任何一处把"营业总收入"写在数字旁边，纯文本正则永远抽不到，
        结果整张表的数字全被判成"无来源/无法核实"，明明数据包里就有。
        这里按列头绑定指标、按首列绑定报告期，表格数字才真正参与核对。
        """
        lines = text.splitlines()
        out: List[Claim] = []
        offset = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip().startswith("|"):
                offset += len(line) + 1
                i += 1
                continue
            # 收集一个连续的表格块
            header_lineno = i
            block: List[Tuple[str, int]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append((lines[i], offset))
                offset += len(lines[i]) + 1
                i += 1
            if len(block) < 3:
                continue
            header_line, header_off = block[0]
            headers = [c.strip() for c in header_line.strip().strip("|").split("|")]
            specs = [self._header_metric(h) for h in headers]
            # 表头没写单位时，读表格上方标题行里的「单位：亿元」
            block_scale = self._block_unit_scale(lines, header_lineno)
            # block[1] 是 |---| 分隔行，从 block[2] 开始是数据行
            for row_line, row_off in block[2:]:
                cells = row_line.strip().strip("|").split("|")
                if len(cells) != len(headers):
                    continue
                period = self._cell_period(cells[0])
                for idx in range(1, len(cells)):
                    spec, header_scale = specs[idx] if idx < len(specs) else (None, None)
                    if spec is None or spec.kind not in ("amount", "percent", "price"):
                        continue
                    cell = cells[idx]
                    m = self._CELL_NUM_RE.search(cell)
                    if not m:
                        continue
                    try:
                        value = float(m.group(1))
                    except ValueError:
                        continue
                    scale = header_scale if header_scale is not None else block_scale
                    if spec.kind == "amount":
                        value = value * scale
                    # 单元格内定位绝对下标，供 _classify_unattributed 去重
                    row_cell_off = row_off + row_line.index(cell)
                    pos = row_cell_off + m.start(1)
                    out.append(Claim(
                        metric=spec.key,
                        label=spec.label,
                        kind=spec.kind,
                        claimed=value,
                        raw=cell.strip(),
                        pos=pos,
                        context=row_line,
                        period=period,
                    ))
        return out

    # -- U2：分期归因（"2024年营收…"不与最新期混用）-------------------
    _YEAR_IN_CTX = re.compile(r"(20\d{2})(?:\s*[-/年]\s*(\d{1,2}))?")

    def _fact_for(self, claim: Claim, fact_base: FactBase):
        if fact_base is None:
            return None
        # 表格解析出的报告期最可靠，优先使用
        if claim.period:
            fact = fact_base.get_for_period(claim.metric, claim.period)
            if fact is not None and fact.value is not None:
                return fact
        ctx = claim.context or ""
        m = self._YEAR_IN_CTX.search(ctx)
        if m:
            period = m.group(1)
            if m.group(2):
                try:
                    period = f"{m.group(1)}-{int(m.group(2)):02d}"
                except (TypeError, ValueError):
                    period = m.group(1)
            fact = fact_base.get_for_period(claim.metric, period)
            if fact is not None and fact.value is not None:
                return fact
        return fact_base.get(claim.metric)

    # -- U1：未归因数字 -------------------------------------------------
    def _classify_unattributed(self, text: str, fact_base: FactBase,
                               claimed_positions: List[int],
                               report: VerificationReport) -> None:
        """把报告里**所有**财务数字与已归因位置对齐，剩下的标为 unsourced。

        这是把"抽查指标词典"升级为"全量数字分类"的关键：
        词典覆盖不到的数字不再被静默放过，而是进入 ``unsourced`` 清单并拉低覆盖率。
        """
        if not text:
            return
        all_facts = self._all_facts(fact_base)
        for mention in extract_numbers(text):
            if mention.kind == KIND_BARE:
                report.ignored_numbers += 1
                continue
            if any(abs(mention.start - pos) <= 2 for pos in claimed_positions):
                continue
            # 先按期精确匹配：文中若写明了报告期（"2026-03-31 的 20.29%"），
            # 就在该期的事实里找，比全局撞值可靠得多
            matched_fact = None
            matched_from_period = False
            ctx_period = self._context_period(mention.context)
            if ctx_period and fact_base is not None:
                bucket = getattr(fact_base, "facts_by_period", {}).get(ctx_period) or {}
                for fact in bucket.values():
                    if (fact is not None and fact.value is not None
                            and self._value_matches_fact(mention, fact)):
                        matched_fact = fact
                        matched_from_period = True
                        break
            # 没写明报告期（或该期找不到）时退到全局匹配。
            # 百分比不走这条：3.70% 这种两位数在全局撞上毛利率/ROE 太容易，
            # 纯靠"数值相等"就说已核实，会放过真正的指标错配。
            if matched_fact is None and mention.kind != KIND_PERCENT:
                for fact in all_facts:
                    if fact is None or fact.value is None:
                        continue
                    if self._value_matches_fact(mention, fact):
                        matched_fact = fact
                        break
            item = {
                "raw": mention.raw,
                "value": format_number(mention.value, mention.kind),
                "kind": mention.kind,
                "context": mention.context.strip(),
            }
            if matched_fact is not None and (mention.kind != KIND_PERCENT or matched_from_period):
                # 数值在事实库里对得上，只是文中没点名指标。
                # 这类不该算「无来源」——否则「有据可查但没点名」会被误报成「疑似编造」。
                item["hint"] = (
                    f"数值与事实库中的「{matched_fact.label}」"
                    f"（{matched_fact.period or '最新期'}）一致，但文中未指明指标"
                )
                item["note"] = "数值已核实，但文中未指明对应指标（建议人工确认是哪一项）"
                report.implicit.append(item)
            else:
                item["hint"] = ""
                item["note"] = ("该数字未归因到任何已知指标，也未在事实库中找到对应值，"
                                "无法核实（需人工复核）")
                report.unsourced.append(item)

    #: 上下文里写明的报告期（"2026-03-31" / "2026年3月31日" / "2026/3/31"）
    _CTX_PERIOD_RE = re.compile(
        r"(20\d{2})\s*[-/年]\s*(\d{1,2})(?:\s*[-/月]\s*(\d{1,2})\s*日?)?"
    )

    @classmethod
    def _context_period(cls, context: str) -> str:
        """从上下文里取出最近的一个报告期，用于"按期精确匹配"。"""
        if not context:
            return ""
        matches = list(cls._CTX_PERIOD_RE.finditer(context))
        if not matches:
            return ""
        m = matches[-1]
        try:
            month = int(m.group(2))
        except (TypeError, ValueError):
            return ""
        if not 1 <= month <= 12:
            return ""
        if m.group(3):
            try:
                day = int(m.group(3))
            except (TypeError, ValueError):
                return ""
            return f"{m.group(1)}-{month:02d}-{day:02d}"
        return f"{m.group(1)}-{month:02d}"

    @staticmethod
    def _all_facts(fact_base) -> List[Any]:
        """事实库里**所有**可核验数值，含各报告期的分期事实。

        修复前这里只取 ``fact_base.facts``（最新期），于是「近五期趋势」表格里
        那些历史期数字全被判成"事实库里没有"，明明数据包里就摆着。
        """
        if not fact_base:
            return []
        facts = list(getattr(fact_base, "facts", {}).values())
        for bucket in getattr(fact_base, "facts_by_period", {}).values():
            facts.extend(bucket.values())
        return facts

    def _value_matches_fact(self, mention, fact) -> bool:
        """未归因数字是否与事实库某条数值吻合（仅作为提示，不据此判定为一致）。"""
        try:
            value = float(fact.value)
        except (TypeError, ValueError):
            return False
        if mention.kind == KIND_PERCENT:
            return fact.unit == "percent" and abs(mention.value - value) <= self.pct_tol + _FLOAT_EPS
        if mention.kind == KIND_AMOUNT:
            if fact.unit != "yuan":
                return False
            denom = abs(value) or 1e-9
            return abs(mention.value - value) / denom <= self.rel_tol + _FLOAT_EPS
        if mention.kind in (KIND_PRICE, KIND_MULTIPLE):
            if fact.unit not in ("price", "yuan"):
                return False
            denom = abs(value) or 1e-9
            return abs(mention.value - value) / denom <= self.rel_tol + _FLOAT_EPS
        return False

    # -- U3：报告内部自相矛盾 -------------------------------------------
    def _find_contradictions(self, claims: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """同一指标、**同一报告期**在文中出现多次且数值不一致 -> 自相矛盾。

        必须按报告期分组：趋势表里「净利同比」2026Q3 是 +869.79%、
        2025 年报是 -62.06%，这两个数字本来就该不同，
        笼统按指标归组会把正常的跨期对比误报成"自相矛盾"。
        """
        grouped: Dict[Any, List[Dict[str, Any]]] = {}
        for c in claims:
            grouped.setdefault((c["metric"], c.get("period") or ""), []).append(c)

        out: List[Dict[str, Any]] = []
        for (metric, period), items in grouped.items():
            if len(items) < 2:
                continue
            values = [it.get("claimed") for it in items if it.get("claimed") is not None]
            if len(values) < 2:
                continue
            kind = items[0].get("kind")
            conflicts = []
            for i in range(len(values)):
                for j in range(i + 1, len(values)):
                    if not self._values_close(values[i], values[j], kind, metric):
                        conflicts.append((values[i], values[j]))
            if len(conflicts) >= 1:
                where = f"（{period}）" if period else ""
                out.append({
                    "metric": metric,
                    "label": items[0]["label"],
                    "period": period,
                    "values": sorted({round(v, 4) for v in values}),
                    "count": len(items),
                    "note": f"同一指标{where}在文中出现 {len(items)} 次，数值不一致："
                            + "、".join(str(v) for v in sorted({round(v, 4) for v in values})),
                })
        out.sort(key=lambda x: (x["metric"], x.get("period") or ""))
        return out

    def _values_close(self, a: float, b: float, kind: Optional[str],
                      metric: str = "") -> bool:
        tol = tol_for(metric, kind or "")
        if kind == "percent":
            return abs(a - b) <= tol + _FLOAT_EPS
        denom = abs(b) or 1e-9
        return abs(a - b) / denom <= tol + _FLOAT_EPS

    #: 从原文数字字面量里抠出数值（含符号与小数）
    _RAW_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

    def _display_slack(self, claim: Claim) -> float:
        """按「写出来的小数位数」折算的绝对容差（与 ``claim.claimed`` 同单位）。

        例：表格里写 ``0.09``（亿元）→ 实际值 0.091071 亿元，显示精度本身就有
        ±0.005 亿元。若坚持 0.5% 的相对容差，这个"正确但只写了两位小数"的数字
        会被判成矛盾。按半个最低位放大，比的才是"数字对不对"而不是"位数够不够"。
        """
        raw = (claim.raw or "").strip()
        m = self._RAW_NUM_RE.search(raw)
        if not m:
            return 0.0
        token = m.group(0)
        try:
            raw_value = float(token)
        except ValueError:
            return 0.0
        if raw_value == 0:
            return 0.0
        decimals = len(token.split(".")[1]) if "." in token else 0
        # claimed 与原文数字之间的倍率（元/亿元 换算后就会出现倍率）
        scale = abs(claim.claimed / raw_value)
        return 0.5 * (10 ** -decimals) * scale

    # ------------------------------------------------------------------
    def _judge(self, claim: Claim, fact) -> Dict[str, Any]:
        # 记下比对所用事实的报告期：判断"自相矛盾"时必须按期间分组，
        # 否则趋势表里不同期的同一指标会被当成互相打架。
        base = {"metric": claim.metric, "label": claim.label, "kind": claim.kind,
                "claimed_raw": claim.raw, "context": claim.context,
                "period": getattr(fact, "period", "") if fact is not None else ""}
        if fact is None or fact.value is None:
            return {**base, "status": "unverifiable", "claimed": claim.claimed,
                    "fact": None, "note": "事实库未覆盖该指标，无法证实（需人工/更多数据源确认）",
                    "correction": ""}
        # 显示精度带来的绝对误差：表格里写「0.09」亿元，本来就只有 ±0.005 亿的精度，
        # 拿它去套 0.5% 的相对容差必然判成"矛盾"，那是冤枉模型。
        #
        # 百分比不加这层：PCT_TOL / PCT_TOL_CORE 本来就是按"保留 1~2 位小数的舍入空间"
        # 定的，再加上显示精度补偿会重复放宽，把真的写错的百分数也放过去。
        if claim.kind == "percent":
            diff = abs(claim.claimed - fact.value)
            tol = tol_for(claim.metric, claim.kind)
            # 加浮点保护：0.1 在二进制下可能略大于 0.1
            ok = diff <= tol + _FLOAT_EPS
        else:
            denom = abs(fact.value) or 1e-9
            diff = abs(claim.claimed - fact.value)
            ok = (diff
                  <= tol_for(claim.metric, claim.kind) * denom
                  + self._display_slack(claim) + _FLOAT_EPS)

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
                pos = end + offset
                ctx = text[max(0, pos - 40): pos + 40]
                # 以数值在原文中的位置去重，避免长短别名重复抽同一条
                out.setdefault(pos, Claim(spec.key, spec.label, spec.kind, value, raw,
                                          pos=pos, context=ctx))
        return list(out.values())

    # -- 增速指标（同比/环比）：锚点 + 紧邻数字 + 禁止跨指标 -----------
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
        for ym in _growth_re(spec.growth_words).finditer(text):
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
            out.setdefault(pos, Claim(spec.key, spec.label, "percent", num, raw,
                                      pos=pos,
                                      context=text[max(0, pos - 40): pos + 40]))
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
        """从窗口开头读取数值，返回 (值, 原文, 数值在窗口内的偏移)。

        阿拉伯数字优先，中文数字（T1）兜底——否则
        「营业总收入一百五十二点三亿元」只能被当作"无来源数字"，无法归因到指标。
        """
        value, raw, offset = cls._read_value_digits(window, kind, unit_required)
        if value is not None:
            return value, raw, offset
        return cls._read_value_cn(window, kind, unit_required)

    @classmethod
    def _read_value_digits(cls, window: str, kind: str,
                           unit_required: bool = False) -> Tuple[Optional[float], str, int]:
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
        if _YEAR_RE.fullmatch(raw_num):
            # 四位年份只有在后面紧跟日期分隔符时才必须排除。
            # 旧版只看「年」，于是「跌破 20 日均线，2026-10-21」里的 2026
            # 会被当成均线数值，判出一条根本不存在的矛盾。
            nxt = window[m.end(): m.end() + 1]
            if nxt in ("年", "-", "/", "月", "日", "~"):
                return None, "", 0
        unit = m.group(2)
        if unit_required and not unit:
            return None, "", 0
        return num, m.group(0), m.start(1)

    @classmethod
    def _read_value_cn(cls, window: str, kind: str,
                       unit_required: bool = False) -> Tuple[Optional[float], str, int]:
        """中文数字兜底（T1）。"""
        conn = _CONN_RE.match(window)
        start = conn.end() if conn else 0
        hit = match_cn_at(window[start:])
        if hit is None:
            return None, "", 0
        value, raw, has_big, has_pct, has_price_unit = hit
        if kind == "amount":
            # 金额必须有 万/亿 这类大单位（"五十万股"这种计数不算金额）
            if not has_big:
                return None, "", 0
            return value, raw, start
        if kind == "percent":
            if not has_pct:
                return None, "", 0
            return value, raw, start
        # price
        if has_pct or has_big:
            return None, "", 0
        if unit_required and not has_price_unit:
            return None, "", 0
        return value, raw, start
