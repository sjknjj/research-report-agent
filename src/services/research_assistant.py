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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from ..analysis.chain import chain_to_text, find_chain
from ..analysis.chain_viz import chain_graph_html
from ..common.logger import get_logger
from ..data.data_service import CompanyBundle, DataService
from ..data.sources import DataSourceError
from ..llm.client import LLMClient
from ..rag.vector_store import VectorStore
from ..verify.facts import FactBase
from ..verify.semantic_check import SemanticChecker
from ..verify.verifier import CrossVerifier, VerificationReport

logger = get_logger("services.assistant")

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
        self.data = data_service or DataService()
        self.verifier = verifier or CrossVerifier()
        self.semantic = SemanticChecker(self.llm)
        self.repair_rounds = max(0, settings.max_review_rounds)
        self.vector_store = VectorStore(
            persist_path=os.path.join(os.path.dirname(__file__), "..", "..", "data", "rag_index.json")
        )
        # v3.7：用户选中的数据源列表（默认只用 akshare）
        self.sources = sources or ["akshare"]

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
                repaired = self.llm.chat(
                    messages + [
                        {"role": "assistant", "content": final_text},
                        {"role": "user", "content": gap_msg},
                    ],
                    stage="company_brief",
                    context=context,
                    temperature=0.2,
                )
                # 重写后再跑一次数字校验，确保没引入新数字错误
                recheck = self.verifier.verify(repaired, fact_base)
                if recheck.n_mismatch == 0:
                    final_text = repaired
                    logger.info("语义重写完成，数字复核通过")

        # 把本次报告存入向量库，供后续 RAG 检索
        try:
            self.vector_store.add(
                text=final_text[:2000],
                source=f"{bundle.name}({bundle.symbol})",
                metadata={"symbol": bundle.symbol, "name": bundle.name},
            )
            self.vector_store.save()
        except Exception:
            pass

        # 三大报表勾稽校验（v3.8新增）
        statements_report = None
        try:
            import akshare as ak
            from src.verify.statements import check_statements

            # 抓三大报表
            try:
                balance_df = ak.stock_financial_report_sina(stock=symbol, symbol="资产负债表")
                income_df = ak.stock_financial_report_sina(stock=symbol, symbol="利润表")
                cashflow_df = ak.stock_financial_report_sina(stock=symbol, symbol="现金流量表")
            except Exception:
                balance_df = income_df = cashflow_df = None

            # 取最新一期数据，转换成字典
            def _df_to_dict(df, col_map):
                if df is None or (hasattr(df, "empty") and df.empty):
                    return {}
                latest = df.iloc[0]  # 第一行是最新期
                result = {}
                for cn_name, en_name in col_map.items():
                    if cn_name in latest.index:
                        val = latest[cn_name]
                        try:
                            result[en_name] = float(val)
                        except (ValueError, TypeError):
                            pass
                return result

            balance = _df_to_dict(balance_df, {
                "资产总计": "total_assets",
                "负债合计": "total_liabilities",
                "所有者权益(或股东权益)合计": "total_equity",
            })
            income = _df_to_dict(income_df, {
                "营业收入": "revenue",
                "营业总成本": "operating_cost",
                "营业利润": "operating_profit",
                "利润总额": "total_profit",
                "所得税费用": "income_tax",
                "净利润": "net_profit",
            })
            cashflow = _df_to_dict(cashflow_df, {
                "期初现金及现金等价物余额": "begin_cash",
                "期末现金及现金等价物余额": "end_cash",
                "经营活动产生的现金流量净额": "net_op_cash",
                "投资活动产生的现金流量净额": "net_inv_cash",
                "筹资活动产生的现金流量净额": "net_fin_cash",
            })

            statements_report = check_statements(balance=balance, income=income, cashflow=cashflow)

            # 把勾稽结果加到报告末尾
            if statements_report.items:
                st_text = "\n\n---\n\n📋 **三大报表勾稽校验**\n\n"
                st_text += f"勾稽结论：{'✅ 通过' if statements_report.passed else '⚠️ 未通过'}\n\n"
                for item in statements_report.items:
                    icon = "✅" if item.passed else "❌"
                    st_text += f"- {icon} **{item.name}**：{item.detail}\n"
                final_text = final_text + st_text
        except Exception as exc:
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
            if best_report.passed:
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
    def _find_col(columns: List[str], *keywords: str) -> Optional[str]:
        for col in columns:
            if any(k in str(col) for k in keywords):
                return col
        return None

    def _industry_snapshot(self, industry_name: str):
        try:
            df = self.data.get_industry_constituents(industry_name)
        except DataSourceError as exc:
            logger.warning("行业实时接口不可用，使用样例快照：%s", exc)
            return self._sample_industry_snapshot(industry_name)

        cols = list(df.columns)
        code_col = self._find_col(cols, "代码")
        name_col = self._find_col(cols, "名称")
        pct_col = self._find_col(cols, "涨跌幅")
        mv_col = self._find_col(cols, "总市值")
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
        return stats, table, False

    @staticmethod
    def _sample_industry_snapshot(industry_name: str):
        stats = {
            "count": 3, "up_count": 2, "avg_pct": 0.82, "median_pct": 0.65,
            "leaders": "样本A(+1.62%)、样本B(+0.65%)、样本C(+0.19%)",
            "note": "实时板块接口不可用，此为离线演示快照（非真实行情）",
        }
        table = (
            "代码    名称     涨跌幅(%)\n"
            "000001  样本A    1.62\n000002  样本B    0.65\n000003  样本C    0.19"
        )
        return stats, table, True
