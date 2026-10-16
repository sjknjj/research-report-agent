"""轻量化投研助手。

三大能力：
- :meth:`company_brief`     公司投研速览（行情 + 财务 + 资讯 → 大模型 → 数字交叉校验 → 自动纠错）
- :meth:`extract_announcement` 公告要点提炼（结构化要点，数字与财报交叉校验）
- :meth:`summarize_industry`   行业信息汇总（板块成分快照聚合 → 大模型概述）

反幻觉闭环（核心）：
    已核实事实包(输入锁定) → 大模型生成 → CrossVerifier 逐条核对数字
    → 若存在矛盾，把“正确值纠错清单”回填让模型重写 → 再次核对
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ..analysis.chain import chain_to_text, find_chain
from ..analysis.chain_viz import chain_graph_html
from ..common.logger import get_logger
from ..data.data_service import DataService
from ..data.sources import DataSourceError
from ..llm.client import LLMClient, LLMError
from ..rag.vector_store import VectorStore
from ..verify.facts import FactBase
from ..verify.semantic_check import SemanticChecker
from ..verify.statements import (
    TABLE_ALIASES,
    check_statements,
    normalize_statement_row,
)
from ..verify.verifier import CrossVerifier

logger = get_logger("services.assistant")

# F3：离线行业快照的数据文件（统计值必须与该 CSV 一一对应，而非硬编码）
_SAMPLE_INDUSTRY_CSV = (
    Path(__file__).resolve().parents[2] / "data" / "samples" / "sample_industry_raw.csv"
)

_BRIEF_SYSTEM = (
    "你是一名严谨的 A 股投研分析师。请严格依据用户给出的【已核实数据包】撰写公司投研速览，"
    "要求：1) 只能使用数据包中的数字，数据包没有的数字一律写“数据未覆盖”，严禁编造或调用记忆中的数字；"
    "2) 每个关键数字后用括号标注口径/报告期；3) 分『财务表现、行情表现、资讯梳理、风险提示』四部分，"
    "使用 Markdown；4) 客观中立，不做买卖建议，结尾注明不构成投资建议。"
)
_ANNOUNCE_SYSTEM = (
    "你是公告解读分析师。请把公告原文提炼为结构化要点，包含：事件类型、关键数据、经营要点、"
    "对公司的潜在影响、风险提示。要求：关键数据必须原样取自公告原文，不得计算、估算或补充原文没有的数字，"
    "使用 Markdown 输出。"
)
_INDUSTRY_SYSTEM = (
    "你是行业研究员。请仅依据用户提供的板块成分快照撰写行业汇总，"
    "不得引入快照之外的涨跌幅、市值、排名数字；快照未覆盖的信息用定性表述并注明“需补充数据”。"
)


@dataclass
class AssistantResult:
    text: str
    verification: Optional[Dict[str, Any]] = None
    bundle: Optional[Dict[str, Any]] = None
    fact_pack: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)
    raw_bundle: Any = None  # CompanyBundle 对象（含 DataFrame，供图表使用）


class ResearchAssistant:
    """投研助手编排器。"""

    def __init__(
        self,
        settings: Any,
        data_service: Optional[DataService] = None,
        verifier: Optional[CrossVerifier] = None,
        sources: Optional[List[str]] = None,
    ):
        self.settings = settings
        self.llm = LLMClient(settings)
        # 用户显式勾选的数据源（Web 首页 / 配置）必须真正生效：
        # 之前 self.sources 只是被保存下来、从未被读取，多选等于摆设。
        self.sources = [s for s in (sources or []) if s]
        if data_service is not None:
            self.data = data_service
        else:
            manager = None
            if self.sources:
                from types import SimpleNamespace

                from ..data.manager import build_manager_from_settings

                # 把勾选的数据源包成 settings 形状，交给工厂函数构造数据源实例
                cfg = SimpleNamespace(
                    data_sources=self.sources,
                    cross_check_enabled=getattr(settings, "cross_check_enabled", True),
                    auto_failover=getattr(settings, "auto_failover", True),
                )
                try:
                    manager = build_manager_from_settings(cfg)
                except Exception as exc:  # noqa: BLE001 - 构造失败时退回单源
                    logger.warning("多数据源管理器构造失败，退回单数据源：%s", exc)
                    manager = None
            self.data = DataService(manager=manager, settings=settings)
        self.verifier = verifier or CrossVerifier()
        self.semantic = SemanticChecker(self.llm)
        self.repair_rounds = max(0, settings.max_review_rounds)
        self.vector_store = VectorStore(
            persist_path=os.path.join(os.path.dirname(__file__), "..", "..", "data", "rag_index.json")
        )

    # ------------------------------------------------------------------
    def company_brief(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        prefer_live: bool = True,
    ) -> AssistantResult:
        bundle = self.data.get_company_bundle(symbol, start_date, end_date, prefer_live)
        fact_base = FactBase(bundle)
        fact_pack = fact_base.fact_pack_text()

        chain_info = find_chain(bundle.symbol, bundle.name)
        chain_text = chain_to_text(chain_info) if chain_info else ""

        rag_results = self.vector_store.search(
            f"{bundle.name} {bundle.symbol} {bundle.financial_summary.get('latest_period','')}",
            top_k=2,
        )
        rag_text = ""
        if rag_results:
            rag_text = "\n\n【历史相关报告片段参考】\n" + "\n".join(
                f"- [{r['source']}] {r['text'][:200]}" for r in rag_results
            )

        extra_pack = ""
        if chain_text:
            extra_pack += "\n\n" + chain_text
        if rag_text:
            extra_pack += rag_text

        messages = [
            {"role": "system", "content": _BRIEF_SYSTEM},
            {"role": "user", "content": f"{fact_pack}{extra_pack}\n\n请据此撰写 {bundle.name} 的投研速览。"},
        ]
        context = {
            "name": bundle.name,
            "symbol": bundle.symbol,
            "period": bundle.financial_summary.get("latest_period", ""),
            "financial": bundle.financial_summary.get("latest", {}),
            "price": bundle.price_summary,
            "news": bundle.news,
        }
        draft = self.llm.chat(messages, stage="company_brief", context=context, temperature=0.35)
        final_text, verification = self._verify_and_repair(draft, fact_base, messages, context)

        # 语义层校验：在线模式下检查生成文本是否有无来源的定性判断/因果归因
        semantic_report = None
        if not self.llm.offline:
            semantic_report = self.semantic.check(final_text, fact_pack)
            if not semantic_report.passed and semantic_report.unsupported:
                logger.warning("语义校验 faithfulness=%.1f%%，%d 条声明无事实包支撑，回填重写",
                               semantic_report.faithfulness_score * 100,
                               len(semantic_report.unsupported))
                gap_msg = (
                    "你上一版存在以下无事实依据的判断，请删除或改写为仅基于已核实数据包的表述：\n"
                    + "\n".join(f"- {c['claim']}（{c['reason']}）"
                                for c in semantic_report.unsupported[:5])
                )
                try:
                    repaired = self.llm.chat(
                        messages + [
                            {"role": "assistant", "content": final_text},
                            {"role": "user", "content": gap_msg},
                        ],
                        stage="company_brief",
                        context=context,
                        temperature=0.2,
                    )
                except LLMError as exc:
                    # 重写失败不能静默丢弃：保留原稿，但明确记录未采纳重写
                    logger.warning("语义重写调用失败，保留原稿：%s", exc)
                    repaired = ""
                if repaired:
                    # 重写后再跑一次数字校验，确保没引入新数字错误；
                    # 只有复核通过才采纳，并把 verification 换成重写后的结果。
                    recheck = self.verifier.verify(repaired, fact_base)
                    if recheck.n_mismatch == 0:
                        final_text = repaired
                        verification = recheck
                        logger.info("语义重写完成，数字复核通过并已采纳")
                    else:
                        logger.warning(
                            "语义重写后数字复核未通过（%d 处矛盾），未采纳重写，保留原稿",
                            recheck.n_mismatch,
                        )

        # 把本次报告存入向量库，供后续 RAG 检索（失败不影响主流程，但必须留痕）
        try:
            self.vector_store.add(
                text=final_text[:2000],
                source=f"{bundle.name}({bundle.symbol})",
                metadata={"symbol": bundle.symbol, "name": bundle.name},
            )
            self.vector_store.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("向量库写入/保存失败（不影响主流程）：%s", exc)

        # 三大报表勾稽校验（v3.8新增；F2：字段映射健壮化 + 字段级诊断留痕）
        statements_report = None
        statements_error = ""
        statements_tables: Dict[str, Any] = {}
        statements_diagnostic: Optional[str] = None
        statements_skipped = self.settings.effective_mode == "offline"
        if statements_skipped:
            # 离线模式必须直接跳过：不发网络请求，也不伪造结果
            logger.info("离线模式跳过三大报表勾稽校验（不发起网络请求）")
        else:
            table_names = (
                ("balance", "资产负债表"),
                ("income", "利润表"),
                ("cashflow", "现金流量表"),
            )
            try:
                import akshare as ak

                # 新浪财报接口需要 sh600519 / sz000858 形式，传纯 6 位数字必然失败
                sina_symbol = self._to_sina_symbol(symbol)
                logger.info("三大报表勾稽：标的 %s → %s", symbol, sina_symbol)

                # 三张表分别抓取，单张失败不影响其它两张；失败原因逐表留痕
                frames: Dict[str, Any] = {}
                fetch_errors: Dict[str, str] = {}
                for attr, table_name in table_names:
                    try:
                        frames[attr] = ak.stock_financial_report_sina(
                            stock=sina_symbol, symbol=table_name
                        )
                    except Exception as exc:  # noqa: BLE001
                        frames[attr] = None
                        fetch_errors[attr] = f"抓取{table_name}失败：{exc}"
                        logger.warning("抓取%s失败（%s）：%s", table_name, sina_symbol, exc)

                statements_report, statements_tables = self._parse_statement_frames(
                    frames, fetch_errors
                )
                statements_diagnostic = self._statements_diagnostic(statements_tables)
                if statements_diagnostic:
                    # 缺科目不是“数据不足”这么简单，必须留下可人工核对的具体线索
                    logger.warning("三大报表勾稽字段诊断：%s", statements_diagnostic)
                if statements_tables and all(t.get("error") for t in statements_tables.values()):
                    statements_error = "三张报表均未成功获取或解析失败"

                # 把勾稽结果加到报告末尾
                if statements_report.items:
                    st_text = "\n\n---\n\n📋 **三大报表勾稽校验**\n\n"
                    # 三态结论：缺科目时显示"部分通过"，而不是笼统的"通过"
                    st_verdict = getattr(statements_report, "verdict", None) or (
                        "pass" if statements_report.passed else "fail")
                    st_icon = {"pass": "✅", "partial": "⚠️"}.get(st_verdict, "❌")
                    st_text += f"勾稽结论：{st_icon} {statements_report.verdict_text}\n\n"
                    if statements_diagnostic:
                        st_text += f"> ⚠️ {statements_diagnostic}\n\n"
                    for item in statements_report.items:
                        # 缺科目的跳过项不是"不通过"，用 ⚪ 区分，避免误导
                        if not getattr(item, "checked", True):
                            icon = "⚪"
                        else:
                            icon = "✅" if item.passed else "❌"
                        st_text += f"- {icon} **{item.name}**：{item.detail}\n"
                    final_text = final_text + st_text
                else:
                    statements_error = statements_error or "接口返回的报表未包含可校验科目"
                    logger.warning("三大报表勾稽：%s", statements_error)
            except Exception as exc:  # noqa: BLE001
                statements_error = str(exc)
                logger.warning("三大报表勾稽校验失败（不影响主流程）：%s", exc)

        return AssistantResult(
            text=final_text,
            verification=verification.to_dict(),
            bundle=bundle.to_dict(),
            fact_pack=fact_pack,
            raw_bundle=bundle,
            extra={
                "semantic": semantic_report.to_dict() if semantic_report else None,
                "chain": chain_info.get("chain") if chain_info else None,
                "chain_graph": chain_graph_html(chain_info, bundle.symbol) if chain_info else "",
                "technicals": bundle.technicals,
                "statements": statements_report.to_dict() if statements_report else None,
                "statements_skipped": statements_skipped,
                "statements_error": statements_error or None,
                # F2：字段级诊断——区分“接口抓取失败”与“列名对不上/数值脏”
                "statements_tables": statements_tables or None,
                "statements_diagnostic": statements_diagnostic,
                "statements_matched_fields": self._collect_statement_fields(
                    statements_tables, "matched"),
                "statements_missing_fields": self._collect_statement_fields(
                    statements_tables, "missing"),
                "statements_unparsable_fields": self._collect_statement_fields(
                    statements_tables, "unparsable"),
            },
        )

    # ------------------------------------------------------------------
    def extract_announcement(
        self,
        announcement_text: str,
        title: str = "公司公告",
        symbol: Optional[str] = None,
        prefer_live: bool = True,
    ) -> AssistantResult:
        if not announcement_text or not announcement_text.strip():
            raise ValueError("公告原文为空")

        messages = [
            {"role": "system", "content": _ANNOUNCE_SYSTEM},
            {"role": "user", "content": f"公告标题：{title}\n公告原文：\n{announcement_text}"},
        ]
        text = self.llm.chat(
            messages,
            stage="announcement",
            context={"title": title, "excerpt": announcement_text[:600]},
            temperature=0.25,
        )

        verification = None
        bundle_dict = None
        # 若提供股票代码，则把提炼结果与已清洗财报做交叉校验（公告 vs 财报）
        if symbol:
            try:
                bundle = self.data.get_company_bundle(symbol, prefer_live=prefer_live)
                fact_base = FactBase(bundle)
                report = self.verifier.verify(text, fact_base)
                verification = report.to_dict()
                bundle_dict = bundle.to_dict()
                logger.info("公告数字与财报交叉校验：%s", report.render_text().splitlines()[0])
            except DataSourceError as exc:
                logger.info("公告交叉校验跳过（无数据包）：%s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("公告交叉校验失败（不影响公告摘要）：%s", exc)

        return AssistantResult(text=text, verification=verification, bundle=bundle_dict)

    # ------------------------------------------------------------------
    def summarize_industry(self, industry_name: str) -> AssistantResult:
        if self.settings.effective_mode == "offline":
            stats, table, used_sample = self._sample_industry_snapshot(industry_name)
        else:
            stats, table, used_sample = self._industry_snapshot(industry_name)
        messages = [
            {"role": "system", "content": _INDUSTRY_SYSTEM},
            {"role": "user", "content": f"板块：{industry_name}\n聚合统计：{stats}\n成分明细：\n{table}"},
        ]
        text = self.llm.chat(
            messages,
            stage="industry",
            context={"industry": industry_name, "stats": stats},
            temperature=0.35,
        )
        return AssistantResult(
            text=text,
            extra={"stats": stats, "used_sample": used_sample},
        )

    # ------------------------------------------------------------------
    def _verify_and_repair(
        self,
        draft: str,
        fact_base: FactBase,
        messages: List[Dict[str, str]],
        context: Dict[str, Any],
    ):
        report = self.verifier.verify(draft, fact_base)
        best_text, best_report = draft, report
        for round_idx in range(self.repair_rounds):
            # 重写只针对“数字与事实矛盾”，不用严格的 passed：
            # passed 将扩展为“无矛盾且覆盖率达标”的三态判定，覆盖率不足不应触发无意义重写。
            if not best_report.has_mismatch:
                break
            logger.warning("第 %d 轮发现 %d 处数字矛盾，回填纠错值重写",
                           round_idx + 1, best_report.n_mismatch)
            repair_msg = (
                "你上一版存在与已核实数据矛盾的数字，请严格按下列纠错值重写，"
                "其余内容保持，不得再出现矛盾：\n"
                + "\n".join(f"- {c}" for c in best_report.corrections)
            )
            repaired = self.llm.chat(
                messages + [
                    {"role": "assistant", "content": best_text},
                    {"role": "user", "content": repair_msg},
                ],
                stage="company_brief",
                context=context,
                temperature=0.2,
            )
            new_report = self.verifier.verify(repaired, fact_base)
            if new_report.n_mismatch <= best_report.n_mismatch:
                best_text, best_report = repaired, new_report
        logger.info("交叉校验完成：%s", best_report.render_text().splitlines()[0])
        return best_text, best_report

    # ------------------------------------------------------------------
    @staticmethod
    def _to_sina_symbol(symbol: str) -> str:
        """把纯 6 位 A 股代码转成新浪财经需要的带交易所前缀形式。

        新浪 ``stock_financial_report_sina`` 要求 ``sh600519`` / ``sz000858``，
        直接传 ``600519`` 会必然失败（历史上被静默吞掉）。
        """
        text = str(symbol or "").strip().lower()
        if text.startswith(("sh", "sz", "bj")):
            return text
        digits = re.sub(r"\D", "", text)
        if not digits:
            return text
        if digits.startswith("6"):
            return f"sh{digits}"
        if digits.startswith(("0", "3")):
            return f"sz{digits}"
        if digits.startswith(("4", "8")):
            return f"bj{digits}"
        return f"sh{digits}"

    # -- F2：三大报表字段解析与诊断 -----------------------------------
    @staticmethod
    def _parse_statement_frames(
        frames: Dict[str, Any],
        fetch_errors: Optional[Dict[str, str]] = None,
    ):
        """把三张报表 DataFrame 解析为勾稽校验输入，并返回字段级诊断。

        返回 ``(StatementCheckReport, tables)``。``tables`` 按表记录：
        ``fetched``（是否拿到数据）、``error``（失败原因）、
        ``matched``/``missing``/``unparsable``（中文科目）、
        ``columns``（中文科目 → 接口实际列名）、``methods``（匹配方式）。
        这样上层能区分“接口抓取失败”与“接口列名变化/数值脏”，而不是笼统报“数据不足”。
        """
        fetch_errors = fetch_errors or {}
        values: Dict[str, Dict[str, Any]] = {"balance": {}, "income": {}, "cashflow": {}}
        tables: Dict[str, Dict[str, Any]] = {}
        for attr, table_name in (
            ("balance", "资产负债表"),
            ("income", "利润表"),
            ("cashflow", "现金流量表"),
        ):
            df = frames.get(attr)
            diag: Dict[str, Any] = {
                "table": table_name,
                "fetched": False,
                "error": fetch_errors.get(attr),
                "matched": [],
                "missing": [],
                "unparsable": [],
                "columns": {},
                "methods": {},
            }
            if df is None or (hasattr(df, "empty") and df.empty):
                diag["error"] = diag["error"] or f"{table_name}接口未返回数据或返回空表"
            else:
                try:
                    latest = df.iloc[0]  # 第一行是最新期
                    row = {str(k): v for k, v in latest.items()}
                    normalized = normalize_statement_row(row, TABLE_ALIASES[attr])
                    values[attr] = dict(normalized.values)
                    diag["fetched"] = True
                    diag.update(normalized.to_diagnostics())
                except Exception as exc:  # noqa: BLE001
                    diag["error"] = f"{table_name}解析失败：{exc}"
            tables[attr] = diag

        report = check_statements(
            balance=values["balance"], income=values["income"], cashflow=values["cashflow"]
        )
        return report, tables

    @staticmethod
    def _statements_diagnostic(tables: Dict[str, Any]) -> Optional[str]:
        """汇总字段级诊断；缺科目时给出明确、可执行的人工核对提示。"""
        missing: List[str] = []
        unparsable: List[str] = []
        for attr in ("balance", "income", "cashflow"):
            diag = tables.get(attr) or {}
            missing.extend(diag.get("missing") or [])
            unparsable.extend(diag.get("unparsable") or [])
        missing = list(dict.fromkeys(missing))          # 去重且保持顺序
        unparsable = list(dict.fromkeys(unparsable))

        parts: List[str] = []
        if missing:
            parts.append(
                "未获取到以下科目：" + "、".join(missing)
                + "（该接口字段可能变化，建议人工核对）"
            )
        if unparsable:
            parts.append(
                "以下科目已匹配到列名但数值无法解析（可能为 --/空值），已按缺失处理："
                + "、".join(unparsable)
            )
        return "；".join(parts) if parts else None

    @staticmethod
    def _collect_statement_fields(tables: Dict[str, Any], key: str) -> List[str]:
        """按表汇总 ``matched``/``missing``/``unparsable`` 字段（去重、保持顺序）。"""
        out: List[str] = []
        for attr in ("balance", "income", "cashflow"):
            diag = tables.get(attr) or {}
            out.extend(diag.get(key) or [])
        return list(dict.fromkeys(out))

    @staticmethod
    def _find_col(columns: List[str], *keywords: str) -> Optional[str]:
        for col in columns:
            if any(k in str(col) for k in keywords):
                return col
        return None

    @staticmethod
    def _aggregate_industry_df(df):
        """从板块成分 DataFrame 计算聚合统计与展示表格（在线/样例共用同一口径）。"""
        cols = list(df.columns)
        code_col = ResearchAssistant._find_col(cols, "代码")
        name_col = ResearchAssistant._find_col(cols, "名称")
        pct_col = ResearchAssistant._find_col(cols, "涨跌幅")
        mv_col = ResearchAssistant._find_col(cols, "总市值")
        pct = pd.to_numeric(df[pct_col], errors="coerce") if pct_col else pd.Series(dtype=float)
        stats = {
            "count": int(len(df)),
            "up_count": int((pct > 0).sum()),
            "avg_pct": round(float(pct.mean()), 2) if not pct.empty else None,
            "median_pct": round(float(pct.median()), 2) if not pct.empty else None,
        }
        if pct_col and name_col:
            top = df.assign(_pct=pct).sort_values("_pct", ascending=False).head(3)
            stats["leaders"] = "、".join(
                f"{r[name_col]}({r['_pct']:.2f}%)" for _, r in top.iterrows()
            )
        show_cols = [c for c in [code_col, name_col, pct_col, mv_col] if c]
        table = df[show_cols].head(20).to_string(index=False)
        return stats, table

    def _industry_snapshot(self, industry_name: str):
        try:
            df = self.data.get_industry_constituents(industry_name)
        except DataSourceError as exc:
            logger.warning("行业实时接口不可用，使用样例快照：%s", exc)
            return self._sample_industry_snapshot(industry_name)

        stats, table = self._aggregate_industry_df(df)
        return stats, table, False

    @staticmethod
    def _sample_industry_snapshot(industry_name: str):
        """离线演示快照：优先读取 ``data/samples/sample_industry_raw.csv`` 并据此计算。

        F3：统计值不再硬编码，而是与仓库里的样例 CSV 一一对应；
        仅当 CSV 缺失或损坏时才退回极简内置兜底，并在日志中说明。
        """
        try:
            if _SAMPLE_INDUSTRY_CSV.exists():
                df = pd.read_csv(_SAMPLE_INDUSTRY_CSV, dtype=str)
                stats, table = ResearchAssistant._aggregate_industry_df(df)
                stats["note"] = (
                    "实时板块接口不可用，此为离线演示快照（读自 "
                    f"{_SAMPLE_INDUSTRY_CSV.name}，非真实行情）"
                )
                return stats, table, True
            logger.warning("离线行业样例文件不存在（%s），退回内置极简兜底", _SAMPLE_INDUSTRY_CSV)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取离线行业样例失败（%s），退回内置极简兜底：%s",
                           _SAMPLE_INDUSTRY_CSV, exc)

        # 极简兜底：明确标注无数据，绝不冒充真实行情
        stats = {
            "count": 0, "up_count": 0, "avg_pct": None, "median_pct": None,
            "leaders": "",
            "note": "离线样例文件缺失，无可用板块快照（非真实行情）",
        }
        table = "（离线样例文件缺失，无成分明细）"
        return stats, table, True
