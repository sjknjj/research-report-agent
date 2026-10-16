"""三大报表勾稽校验。

对资产负债表、利润表、现金流量表的关键会计恒等式进行确定性核对，
识别「报表不平」「跨表勾稽不一致」「现金流与利润背离」等问题。
数据口径以万元为单位，容差默认 0.5%（允许四舍五入误差）。

不依赖 AKShare 固定字段：上层抓到什么科目就传什么，缺科目跳过对应检查。
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CheckItem:
    name: str
    formula: str
    passed: bool
    detail: str
    severity: str = "error"          # error（不平）/ warn（背离）/ info
    checked: bool = True             # False = 缺科目，未真正执行该勾稽


@dataclass
class StatementCheckReport:
    items: List[CheckItem] = field(default_factory=list)

    @property
    def data_sufficient(self) -> bool:
        """是否至少完成了一项可执行的勾稽校验。"""
        return any(i.checked for i in self.items)

    @property
    def passed(self) -> bool:
        # 修复 fail-open：一个字段都没有时绝不能报“通过”
        if not self.data_sufficient:
            return False
        return not any(i.severity == "error" and i.checked and not i.passed for i in self.items)

    # ---- 三态结论（与 verifier 的 T1 一致）------------------------------
    @property
    def verdict(self) -> str:
        """``pass`` / ``partial`` / ``fail`` / ``insufficient``。

        缺科目时不能简单说"通过"——只完成了一部分勾稽，用户需要知道覆盖不全。
        """
        if not self.data_sufficient:
            return "insufficient"
        if self.n_errors:
            return "fail"
        if self.n_skipped:
            return "partial"
        return "pass"

    @property
    def verdict_text(self) -> str:
        return {
            "pass": "通过",
            "partial": "部分通过（有科目未获取，结论不完整，需人工核对）",
            "fail": "未通过",
            "insufficient": "数据不足（未完成任何勾稽校验）",
        }[self.verdict]

    @property
    def n_errors(self) -> int:
        return sum(1 for i in self.items if i.checked and i.severity == "error" and not i.passed)

    @property
    def n_warnings(self) -> int:
        return sum(1 for i in self.items if i.checked and i.severity == "warn" and not i.passed)

    @property
    def n_skipped(self) -> int:
        return sum(1 for i in self.items if not i.checked)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "verdict_text": self.verdict_text,
            "status": "ok" if self.data_sufficient else "insufficient",
            "data_sufficient": self.data_sufficient,
            "n_errors": self.n_errors,
            "n_warnings": self.n_warnings,
            "n_skipped": self.n_skipped,
            "items": [
                {"name": i.name, "formula": i.formula, "passed": i.passed,
                 "detail": i.detail, "severity": i.severity, "checked": i.checked}
                for i in self.items
            ],
        }

    def render_text(self) -> str:
        head = (f"三表勾稽结论：{self.verdict_text}｜"
                f"错误 {self.n_errors}、预警 {self.n_warnings}、"
                f"跳过 {self.n_skipped}、共检查 {len(self.items)} 项")
        rows = [head]
        for i in self.items:
            if not i.checked:
                mark = "–"
            else:
                mark = "✓" if i.passed else ("✗" if i.severity == "error" else "!")
            rows.append(f"  [{mark}] {i.name}：{i.detail}")
        return "\n".join(rows)


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _approx(a: float, b: float, tol: float = 0.005) -> bool:
    if b == 0:
        return abs(a) < max(1.0, abs(a) * tol)
    return abs(a - b) / max(abs(b), 1.0) <= tol


def check_statements(
    balance: Optional[Dict[str, Any]] = None,
    income: Optional[Dict[str, Any]] = None,
    cashflow: Optional[Dict[str, Any]] = None,
    tol: float = 0.005,
) -> StatementCheckReport:
    """对三大报表进行勾稽校验。

    :param balance: 资产负债表科目，键名参考常见中文科目：
        total_assets / total_liabilities / total_equity
    :param income: 利润表：revenue / operating_cost / operating_profit
        / total_profit / income_tax / net_profit（**合并口径**净利润）
    :param cashflow: 现金流量表：begin_cash / end_cash / net_op_cash
        / net_inv_cash / net_fin_cash

    缺少科目时不再静默跳过从而 fail-open，而是生成明确的“跳过（无法核实）”项；
    若没有任何一项可校验，``passed`` 为 False 且 ``to_dict()["status"] == "insufficient"``。
    """
    report = StatementCheckReport()
    balance = balance or {}
    income = income or {}
    cashflow = cashflow or {}

    def _missing(*pairs) -> List[str]:
        return [label for label, value in pairs if _num(value) is None]

    def _skip(name: str, formula: str, missing: List[str], severity: str = "info") -> None:
        report.items.append(CheckItem(
            name, formula, False,
            "缺少科目：" + "、".join(missing) + "（未执行，无法核实）",
            severity, checked=False,
        ))

    # ---------- 资产负债表：资产 = 负债 + 权益 ----------
    ta = _num(balance.get("total_assets"))
    tl = _num(balance.get("total_liabilities"))
    te = _num(balance.get("total_equity"))
    if ta is not None and tl is not None and te is not None:
        rhs = tl + te
        ok = _approx(ta, rhs, tol)
        report.items.append(CheckItem(
            "资产负债表恒等式",
            "资产总计 = 负债合计 + 所有者权益合计",
            ok,
            f"{ta:,.2f} vs {tl:,.2f} + {te:,.2f} = {rhs:,.2f}"
            + ("（平衡）" if ok else "（不平衡，差额 %.2f）" % (ta - rhs)),
        ))
    else:
        _skip("资产负债表恒等式", "资产总计 = 负债合计 + 所有者权益合计",
              _missing(("资产总计", balance.get("total_assets")),
                       ("负债合计", balance.get("total_liabilities")),
                       ("所有者权益合计", balance.get("total_equity"))))

    # ---------- 利润表：净利润 = 利润总额 - 所得税 ----------
    # 注意：该恒等式只对**合并口径净利润**成立；归母净利润含少数股东损益，不得混用
    tp = _num(income.get("total_profit"))
    tax = _num(income.get("income_tax"))
    np_ = _num(income.get("net_profit"))
    if tp is not None and tax is not None and np_ is not None:
        expect = tp - tax
        ok = _approx(expect, np_, tol)
        report.items.append(CheckItem(
            "利润表净利润勾稽",
            "净利润(合并口径) = 利润总额 − 所得税费用",
            ok,
            f"{tp:,.2f} − {tax:,.2f} = {expect:,.2f}，披露净利润 {np_:,.2f}"
            + ("（一致）" if ok else "（不一致）"),
        ))
    else:
        _skip("利润表净利润勾稽", "净利润(合并口径) = 利润总额 − 所得税费用",
              _missing(("利润总额", income.get("total_profit")),
                       ("所得税费用", income.get("income_tax")),
                       ("净利润(合并口径)", income.get("net_profit"))))

    # 营业利润 ≈ 营业收入 − 营业成本 − 期间费用（允许有其他收益，给宽松判断）
    rev = _num(income.get("revenue"))
    cost = _num(income.get("operating_cost"))
    op = _num(income.get("operating_profit"))
    if rev is not None and cost is not None and op is not None:
        gross = rev - cost
        # 营业利润不应超过毛利润（除非有其他收益），且不应为负而营收为正
        ok = op <= gross * 1.05
        report.items.append(CheckItem(
            "营业利润合理性",
            "营业利润 ≤ 营业收入 − 营业成本（忽略其他收益）",
            ok,
            f"毛利 {gross:,.2f}，营业利润 {op:,.2f}"
            + ("（合理）" if ok else "（营业利润高于毛利，需核对其他收益）"),
            severity="warn",
        ))
    else:
        _skip("营业利润合理性", "营业利润 ≤ 营业收入 − 营业成本（忽略其他收益）",
              _missing(("营业收入", income.get("revenue")),
                       ("营业成本", income.get("operating_cost")),
                       ("营业利润", income.get("operating_profit"))),
              severity="warn")

    # ---------- 现金流量表：期末现金 = 期初 + 三类净额 ----------
    bc = _num(cashflow.get("begin_cash"))
    ec = _num(cashflow.get("end_cash"))
    oc = _num(cashflow.get("net_op_cash"))
    ic = _num(cashflow.get("net_inv_cash"))
    fc = _num(cashflow.get("net_fin_cash"))
    if all(x is not None for x in (bc, ec, oc, ic, fc)):
        expect = bc + oc + ic + fc
        ok = _approx(expect, ec, tol)
        report.items.append(CheckItem(
            "现金流量表勾稽",
            "期末现金 = 期初现金 + 经营/投资/筹资净额",
            ok,
            f"{bc:,.2f} + ({oc:,.2f}) + ({ic:,.2f}) + ({fc:,.2f}) = {expect:,.2f}，"
            f"披露期末 {ec:,.2f}" + ("（平衡）" if ok else "（不平衡）"),
        ))
    else:
        _skip("现金流量表勾稽", "期末现金 = 期初现金 + 经营/投资/筹资净额",
              _missing(("期初现金", cashflow.get("begin_cash")),
                       ("期末现金", cashflow.get("end_cash")),
                       ("经营净现金流", cashflow.get("net_op_cash")),
                       ("投资净现金流", cashflow.get("net_inv_cash")),
                       ("筹资净现金流", cashflow.get("net_fin_cash"))))

    # ---------- 跨表：现金流与利润背离（利润质量）----------
    if oc is not None and np_ is not None and np_ != 0:
        ratio = oc / np_
        ok = ratio >= 0.8
        report.items.append(CheckItem(
            "经营现金流/净利润（利润质量）",
            "经营净现金流 / 净利润 应接近或大于 1（>0.8 视为健康）",
            ok,
            f"经营净现金流 {oc:,.2f} / 净利润 {np_:,.2f} = {ratio:.2f}"
            + ("（利润含金量高）" if ok else "（现金回款偏弱，警惕纸面利润）"),
            severity="warn",
        ))
    else:
        _skip("经营现金流/净利润（利润质量）",
              "经营净现金流 / 净利润 应接近或大于 1（>0.8 视为健康）",
              _missing(("经营净现金流", cashflow.get("net_op_cash")),
                       ("净利润(合并口径)", income.get("net_profit"))),
              severity="warn")

    return report


# 常见 AKShare/东财财报字段名 → 标准键名映射，方便上层直接喂 DataFrame 行
FIELD_ALIASES = {
    "total_assets": ["资产总计", "总资产", "total_assets"],
    "total_liabilities": ["负债合计", "总负债", "total_liabilities"],
    "total_equity": ["所有者权益合计", "股东权益合计", "所有者权益(或股东权益)合计", "净资产", "total_equity"],
    "revenue": ["营业总收入", "营业收入", "revenue"],
    "operating_cost": ["营业总成本", "营业成本", "operating_cost"],
    "operating_profit": ["营业利润", "operating_profit"],
    "total_profit": ["利润总额", "total_profit"],
    "income_tax": ["所得税费用", "所得税", "income_tax"],
    # 合并口径净利润：只有它满足“净利润 = 利润总额 − 所得税费用”
    "net_profit": ["净利润", "合并净利润", "net_profit"],
    # 归母净利润单独成键，绝不与合并口径混用
    "net_profit_attr": ["归属于母公司所有者的净利润", "归属于母公司股东的净利润",
                        "归母净利润", "net_profit_attr"],
    "begin_cash": ["期初现金及现金等价物余额", "期初现金余额"],
    "end_cash": ["期末现金及现金等价物余额", "期末现金余额"],
    "net_op_cash": ["经营活动产生的现金流量净额", "经营净现金流"],
    "net_inv_cash": ["投资活动产生的现金流量净额", "投资净现金流"],
    "net_fin_cash": ["筹资活动产生的现金流量净额", "筹资净现金流"],
}


# 标准键 → 中文展示名（用于把“缺哪个科目”如实告诉上层与用户）
FIELD_LABELS: Dict[str, str] = {
    "total_assets": "资产总计",
    "total_liabilities": "负债合计",
    "total_equity": "所有者权益合计",
    "revenue": "营业收入",
    "operating_cost": "营业成本",
    "operating_profit": "营业利润",
    "total_profit": "利润总额",
    "income_tax": "所得税费用",
    "net_profit": "净利润(合并口径)",
    "net_profit_attr": "归母净利润",
    "begin_cash": "期初现金",
    "end_cash": "期末现金",
    "net_op_cash": "经营净现金流",
    "net_inv_cash": "投资净现金流",
    "net_fin_cash": "筹资净现金流",
}

# 每张报表需要关注的科目（顺序即 FIELD_ALIASES 的声明顺序）
TABLE_KEYS: Dict[str, Tuple[str, ...]] = {
    "balance": ("total_assets", "total_liabilities", "total_equity"),
    "income": ("revenue", "operating_cost", "operating_profit", "total_profit",
               "income_tax", "net_profit", "net_profit_attr"),
    "cashflow": ("begin_cash", "end_cash", "net_op_cash", "net_inv_cash", "net_fin_cash"),
}

# 按表拆好的别名表：{"balance": {标准键: [别名…]}, …}
TABLE_ALIASES: Dict[str, Dict[str, List[str]]] = {
    table: {key: FIELD_ALIASES[key] for key in keys}
    for table, keys in TABLE_KEYS.items()
}

# ---------------------------------------------------------------------------
# 字段映射健壮化（F2）
#
# 现实里接口列名会漂移（“所有者权益(或股东权益)合计” vs 全角括号版本），
# 数值也可能是 "--"、空串、千分位、带“万元/亿”单位、全角负号。
# 这里提供确定性且**可诊断**的解析：拿不到确切列名时如实报告“命中了什么、缺了什么”，
# 绝不因为列名对不上就静默返回空 dict（那会伪装成“接口问题/数据不足”）。
# ---------------------------------------------------------------------------

# 视为“明确缺失”的占位符（小写比较）
_MISSING_TOKENS = {"", "--", "-", "—", "――", "/", "n/a", "na", "nan",
                   "none", "null", "无", "不适用"}

# 全角/特殊符号统一映射为半角，便于数值与列名解析
_NORMALIZE_TABLE = str.maketrans({
    "－": "-", "−": "-", "–": "-", "—": "-", "﹣": "-",
    "，": ",", "、": ",", "％": "%", "　": " ",
})

# 金额单位后缀 → 相对“元”的倍率；统一折算到元，避免同表内单位不一致导致勾稽误判
_UNIT_FACTORS: Tuple[Tuple[str, float], ...] = (
    ("万亿", 1e12), ("亿元", 1e8), ("亿", 1e8),
    ("万元", 1e4), ("万", 1e4), ("元", 1.0),
)


def _normalize_text(value: Any) -> str:
    """列名归一化：NFKC（全角→半角）+ 去空白 + 去括号 + 小写。

    去掉括号是为了让「所有者权益(或股东权益)合计」与全角括号版本等价。
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.translate(_NORMALIZE_TABLE)
    text = re.sub(r"[\s()（）\[\]【】]", "", text)
    return text.lower()


def clean_number(value: Any) -> Optional[float]:
    """把接口返回的“脏”数值安全转换为 float。

    无法解析或明确缺失时返回 ``None``（**绝不抛异常**）。支持：
    ``1,234.56`` / ``1234.56万元`` / ``1.5亿`` / 全角负号 ``－12`` /
    空串 / ``--`` / ``None`` / ``NaN``。带“万/亿”单位时统一折算为“元”。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, int):
        return float(value)

    text = unicodedata.normalize("NFKC", str(value)).translate(_NORMALIZE_TABLE).strip()
    if text.lower() in _MISSING_TOKENS:
        return None
    # 去千分位逗号与空格
    text = text.replace(",", "").replace(" ", "")
    if not text:
        return None

    factor = 1.0
    for suffix, mult in _UNIT_FACTORS:
        if text.endswith(suffix):
            factor = mult
            text = text[: -len(suffix)]
            break
    text = text.lstrip("¥$￥")
    if text.lower() in _MISSING_TOKENS or text in ("", "-", "+"):
        return None
    try:
        return float(text) * factor
    except (TypeError, ValueError):
        return None


@dataclass
class ColumnMatchResult:
    """列名匹配结果：命中了哪些标准键、各自用哪种方式、缺了哪些。"""
    columns: Dict[str, str] = field(default_factory=dict)   # 标准键 → 接口实际列名
    methods: Dict[str, str] = field(default_factory=dict)   # 标准键 → exact/normalized/contains
    matched: List[str] = field(default_factory=list)        # 命中的标准键（保持 aliases 顺序）
    missing: List[str] = field(default_factory=list)        # 未命中的标准键

    def to_dict(self) -> Dict[str, Any]:
        return {
            "columns": dict(self.columns),
            "methods": dict(self.methods),
            "matched": list(self.matched),
            "missing": list(self.missing),
        }


def match_columns(
    columns: List[Any],
    aliases: Optional[Dict[str, List[str]]] = None,
) -> ColumnMatchResult:
    """在接口返回的 ``columns`` 中为标准科目寻找对应列。

    匹配优先级（对每个科目独立推进，且一列只归属一个科目）：
    1. **精确匹配**：列名与别名完全相同；
    2. **归一化匹配**：NFKC（全角→半角）+ 去空格 + 去括号 + 大小写不敏感；
    3. **包含匹配**：归一化后列名与别名互相包含（大小写不敏感）。

    返回 :class:`ColumnMatchResult`，包含命中/缺失科目与实际列名，
    便于上层记录“接口字段可能变化”的诊断，而不是静默丢数据。
    """
    aliases = aliases if aliases is not None else FIELD_ALIASES
    col_list = [str(c) for c in (columns or [])]
    norm_cols: Dict[str, str] = {}
    for col in col_list:
        key = _normalize_text(col)
        if key and key not in norm_cols:
            norm_cols[key] = col

    result = ColumnMatchResult()
    claimed: Dict[str, str] = {}  # 实际列名 → 已认领的标准键，保证一列只映射一个科目

    def _claim(std_key: str, col: str, method: str) -> bool:
        if col in claimed and claimed[col] != std_key:
            return False
        claimed[col] = std_key
        result.columns[std_key] = col
        result.methods[std_key] = method
        return True

    # 1) 精确匹配
    for std_key, alias_list in aliases.items():
        for alias in alias_list:
            for col in col_list:
                if col == alias and col not in claimed:
                    _claim(std_key, col, "exact")
                    break
            if std_key in result.columns:
                break

    # 2) 归一化后精确匹配（去空格/去括号、全角转半角、大小写不敏感）
    for std_key, alias_list in aliases.items():
        if std_key in result.columns:
            continue
        for alias in alias_list:
            norm_alias = _normalize_text(alias)
            if not norm_alias:
                continue
            col = norm_cols.get(norm_alias)
            if col and col not in claimed:
                _claim(std_key, col, "normalized")
                break

    # 3) 包含匹配（列名包含别名 或 别名包含列名）
    for std_key, alias_list in aliases.items():
        if std_key in result.columns:
            continue
        for alias in alias_list:
            norm_alias = _normalize_text(alias)
            if not norm_alias:
                continue
            for col in col_list:
                if col in claimed:
                    continue
                norm_col = _normalize_text(col)
                if norm_alias in norm_col or norm_col in norm_alias:
                    _claim(std_key, col, "contains")
                    break
            if std_key in result.columns:
                break

    result.matched = [key for key in aliases if key in result.columns]
    result.missing = [key for key in aliases if key not in result.columns]
    return result


@dataclass
class NormalizedStatementRow:
    """一行财报的解析结果：可用数值 + 命中/缺失/不可解析科目（供上层留痕）。"""
    values: Dict[str, float] = field(default_factory=dict)   # 标准键 → 清洗后的数值
    matched: List[str] = field(default_factory=list)         # 命中列名的标准键（含数值不可解析者）
    missing: List[str] = field(default_factory=list)         # 未命中列名的标准键
    unparsable: List[str] = field(default_factory=list)      # 命中列名但数值缺失/不可解析的标准键
    columns: Dict[str, str] = field(default_factory=dict)    # 标准键 → 实际列名
    methods: Dict[str, str] = field(default_factory=dict)    # 标准键 → 匹配方式
    raw: Dict[str, Any] = field(default_factory=dict)        # 标准键 → 接口原始值（诊断用）

    @property
    def labels_missing(self) -> List[str]:
        return [FIELD_LABELS.get(k, k) for k in self.missing]

    @property
    def labels_unparsable(self) -> List[str]:
        return [FIELD_LABELS.get(k, k) for k in self.unparsable]

    def to_diagnostics(self) -> Dict[str, Any]:
        """转成可直接写入 ``extra`` 的中文诊断字典。"""
        return {
            "matched": [FIELD_LABELS.get(k, k) for k in self.matched],
            "missing": self.labels_missing,
            "unparsable": self.labels_unparsable,
            "columns": {FIELD_LABELS.get(k, k): v for k, v in self.columns.items()},
            "methods": dict(self.methods),
        }


def normalize_statement_row(
    row: Optional[Dict[str, Any]],
    aliases: Optional[Dict[str, List[str]]] = None,
) -> NormalizedStatementRow:
    """把一行原始财报（列名/数值都可能很脏）解析为标准键 → 数值 + 诊断。

    - 列名：精确 → 归一化 → 包含匹配；
    - 数值：``clean_number`` 统一清洗，缺失/``--``/``NaN`` 明确判为不可解析；
    - 返回 :class:`NormalizedStatementRow`，如实记录命中、缺失、不可解析的科目。
    """
    aliases = aliases if aliases is not None else FIELD_ALIASES
    row = row or {}
    row_keys: Dict[str, Any] = {str(k): v for k, v in row.items()}
    match = match_columns(list(row_keys.keys()), aliases)

    out = NormalizedStatementRow(
        columns=dict(match.columns),
        methods=dict(match.methods),
        matched=list(match.matched),
        missing=list(match.missing),
    )
    for std_key in match.matched:
        col = match.columns[std_key]
        raw_val = row_keys.get(col)
        out.raw[std_key] = raw_val
        num = clean_number(raw_val)
        if num is None:
            out.unparsable.append(std_key)
        else:
            out.values[std_key] = num
    return out


def statements_from_row(row: dict) -> Dict[str, Dict[str, Any]]:
    """把一行包含中文科目的财报字典，按表拆成 balance/income/cashflow 三段。

    列名匹配改为走 :func:`normalize_statement_row`（精确→归一化→包含），
    数值统一清洗；无法解析的科目按缺失处理，不再因单个科目脏掉整张表。
    """
    out: Dict[str, Dict[str, Any]] = {"balance": {}, "income": {}, "cashflow": {}}
    for table, keys in TABLE_KEYS.items():
        normalized = normalize_statement_row(row, TABLE_ALIASES[table])
        for std_key in keys:
            if std_key in normalized.values:
                out[table][std_key] = normalized.values[std_key]
    return out
