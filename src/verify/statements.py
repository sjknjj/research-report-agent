"""三大报表勾稽校验。

对资产负债表、利润表、现金流量表的关键会计恒等式进行确定性核对，
识别「报表不平」「跨表勾稽不一致」「现金流与利润背离」等问题。
数据口径以万元为单位，容差默认 0.5%（允许四舍五入误差）。

不依赖 AKShare 固定字段：上层抓到什么科目就传什么，缺科目跳过对应检查。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
        if not self.data_sufficient:
            head = (f"三表勾稽结论：数据不足（未完成任何勾稽校验）｜"
                    f"跳过 {self.n_skipped} 项、共检查 0 项")
        else:
            head = (f"三表勾稽结论：{'通过' if self.passed else '未通过'}｜"
                    f"错误 {self.n_errors}、预警 {self.n_warnings}、共检查 {len(self.items)} 项")
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


def statements_from_row(row: dict) -> Dict[str, Dict[str, Any]]:
    """把一行包含中文科目的财报字典，按表拆成 balance/income/cashflow 三段。"""
    groups = {
        "balance": ("total_assets", "total_liabilities", "total_equity"),
        "income": ("revenue", "operating_cost", "operating_profit", "total_profit",
                   "income_tax", "net_profit", "net_profit_attr"),
        "cashflow": ("begin_cash", "end_cash", "net_op_cash", "net_inv_cash", "net_fin_cash"),
    }
    out: Dict[str, Dict[str, Any]] = {"balance": {}, "income": {}, "cashflow": {}}
    for std_key, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in row:
                val = row[alias]
                for group, keys in groups.items():
                    if std_key in keys:
                        out[group][std_key] = val
                break
    return out
