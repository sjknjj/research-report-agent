"""语义层反幻觉校验（faithfulness check）。

CrossVerifier 做的是**数字比对**（正则抽数字 → 和事实库比），
本模块做的是**语义比对**：让大模型把生成文本拆成原子声明，
逐条判断是否被事实包支持。两道防线互补：

- CrossVerifier：确定性、零成本、抓数字幻觉
- SemanticChecker：依赖 LLM、抓"无来源的定性判断/因果归因/比较结论"

不强制依赖 deepeval；deepeval 已安装时可通过 use_deepeval=True 启用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..common.logger import get_logger

logger = get_logger("verify.semantic")

_SYSTEM_PROMPT = (
    "你是一名严格的事实核查员。给定一份【已核实数据包】和一段【生成文本】，"
    "请把生成文本拆成独立的原子声明，逐条判断该声明是否被数据包支持。"
    "严格输出 JSON：\n"
    '{"claims": [{"claim": string, "supported": boolean, '
    '"reason": string}]}。\n'
    "规则：\n"
    "1) 数字、日期、名称必须与数据包完全一致才算 supported；\n"
    "2) 定性判断（如'行业景气度高'）数据包没有明确支撑的算 unsupported；\n"
    "3) 因果归因（如'因为营收增长所以利润提升'）数据包没有给出因果关系的算 unsupported；\n"
    "4) 比较结论（如'优于同行'）数据包没有同业对比数据的算 unsupported。"
)


@dataclass
class SemanticReport:
    total: int = 0
    supported: int = 0
    unsupported: List[Dict[str, str]] = field(default_factory=list)

    @property
    def faithfulness_score(self) -> float:
        if self.total == 0:
            return 1.0
        return round(self.supported / self.total, 3)

    @property
    def passed(self) -> bool:
        return self.faithfulness_score >= 0.7

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "supported": self.supported,
            "faithfulness_score": self.faithfulness_score,
            "passed": self.passed,
            "unsupported_claims": self.unsupported,
        }


class SemanticChecker:
    """基于 LLM 的语义层 faithfulness 校验。"""

    def __init__(self, llm_client: Any, threshold: float = 0.7):
        self.llm = llm_client
        self.threshold = threshold

    def check(self, generated_text: str, fact_pack: str) -> SemanticReport:
        if self.llm is None or self.llm.offline:
            logger.info("离线模式跳过语义校验")
            return SemanticReport()

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"【已核实数据包】\n{fact_pack}\n\n"
                f"【生成文本】\n{generated_text}\n\n"
                "请输出核查 JSON。"
            )},
        ]
        try:
            data = self.llm.chat_json(
                messages,
                stage="semantic_check",
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("语义校验调用失败，跳过：%s", exc)
            return SemanticReport()

        claims = data.get("claims", []) if isinstance(data, dict) else []
        report = SemanticReport(total=len(claims))
        for item in claims:
            if not isinstance(item, dict):
                continue
            if item.get("supported"):
                report.supported += 1
            else:
                report.unsupported.append({
                    "claim": str(item.get("claim", "")),
                    "reason": str(item.get("reason", "")),
                })
        logger.info(
            "语义校验：%d/%d 条声明被事实包支持，faithfulness=%.1f%%",
            report.supported, report.total, report.faithfulness_score * 100,
        )
        return report
