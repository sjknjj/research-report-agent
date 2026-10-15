"""幻觉率历史统计（3.5新增）。

每次校验完自动存一条记录，用来：
1. 看某只股票/某个模型的历史幻觉率
2. 对比不同模型哪个更靠谱
3. 发现高风险主题（幻觉率特别高的）

修复要点：
- ``render_text()`` 用 ``total_runs`` 判断空（旧代码读不存在的 ``total`` 键，永远显示“暂无”）；
- 补 ``render_markdown()``，供 Web ``/api/stats`` 使用；
- 平均幻觉率按 ``sum(mismatch)/sum(total_claims)`` **加权**，避免条数少的样本拉偏整体。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List


def _weighted_rate(records: List[Dict[str, Any]]) -> float:
    """按 mismatch / total_claims 加权求平均幻觉率。

    记录里缺 total_claims/mismatch 时回退到简单平均，保证历史数据兼容。
    """
    total_claims = 0
    mismatch = 0
    for r in records:
        try:
            total_claims += int(r.get("total_claims", 0) or 0)
            mismatch += int(r.get("mismatch", 0) or 0)
        except (TypeError, ValueError):
            continue
    if total_claims > 0:
        return mismatch / total_claims
    if records:
        rates = []
        for r in records:
            try:
                rates.append(float(r.get("hallucination_rate", 0) or 0))
            except (TypeError, ValueError):
                continue
        if rates:
            return sum(rates) / len(rates)
    return 0.0


class HallucinationStats:
    """幻觉率统计器。"""

    def __init__(self, stats_path: str = "output/.stats.json"):
        self.stats_path = stats_path
        self.records: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.stats_path):
            return []
        try:
            with open(self.stats_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.stats_path) or ".", exist_ok=True)
        with open(self.stats_path, "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False, indent=2)

    def record(
        self,
        topic: str,
        model: str,
        total_claims: int,
        mismatch: int,
        hallucination_rate: float,
        confidence_level: str,
    ) -> None:
        """记录一次校验结果。"""
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "topic": topic,
            "model": model,
            "total_claims": total_claims,
            "mismatch": mismatch,
            "hallucination_rate": hallucination_rate,
            "confidence_level": confidence_level,
        }
        self.records.append(entry)
        # 最多保留 200 条，防止文件太大
        if len(self.records) > 200:
            self.records = self.records[-200:]
        self._save()

    def summary(self) -> Dict[str, Any]:
        """生成统计摘要（空仓时仍返回 total_runs 键）。"""
        if not self.records:
            return {"total_runs": 0, "message": "暂无历史记录"}

        total = len(self.records)
        avg_rate = _weighted_rate(self.records)

        # 按模型分组统计（同样加权）
        by_model: Dict[str, List[Dict[str, Any]]] = {}
        for r in self.records:
            model = str(r.get("model", "未知"))
            by_model.setdefault(model, []).append(r)

        model_ranking = []
        for model, recs in by_model.items():
            model_ranking.append({
                "model": model,
                "runs": len(recs),
                "total_claims": sum(int(x.get("total_claims", 0) or 0) for x in recs),
                "mismatch": sum(int(x.get("mismatch", 0) or 0) for x in recs),
                "avg_hallucination_rate": round(_weighted_rate(recs), 4),
            })
        model_ranking.sort(key=lambda x: x["avg_hallucination_rate"])

        return {
            "total_runs": total,
            "avg_hallucination_rate": round(avg_rate, 4),
            "model_ranking": model_ranking,
            "recent_10": self.records[-10:],
        }

    def render_text(self) -> str:
        """生成可读的统计报告。"""
        s = self.summary()
        if s.get("total_runs", 0) == 0:
            return "暂无历史幻觉率记录。跑几份报告后再来看看吧。"

        lines = [
            f"📊 幻觉率历史统计（共 {s['total_runs']} 次）",
            f"平均幻觉率（按声明条数加权）：{s['avg_hallucination_rate']:.1%}",
            "",
            "🏆 模型可信度排名（越低越好）：",
        ]
        for i, m in enumerate(s["model_ranking"], 1):
            lines.append(f"  {i}. {m['model']}：{m['avg_hallucination_rate']:.1%}（跑了{m['runs']}次）")

        lines.append("")
        lines.append("最近 5 次校验：")
        for r in s["recent_10"][-5:]:
            lines.append(
                f"  [{r['time']}] {r['topic']}："
                f"{r['hallucination_rate']:.1%}（{r['confidence_level']}）"
            )
        return "\n".join(lines)

    def render_markdown(self) -> str:
        """生成 Markdown 统计报告（供 Web /api/stats 使用）。"""
        s = self.summary()
        if s.get("total_runs", 0) == 0:
            return "暂无历史幻觉率记录。跑几份报告后再来看看吧。"

        lines = [
            f"# 📊 幻觉率历史统计（共 {s['total_runs']} 次）",
            "",
            f"- **平均幻觉率（按声明条数加权）**：{s['avg_hallucination_rate']:.1%}",
            "",
            "## 🏆 模型可信度排名（越低越好）",
            "",
            "| 排名 | 模型 | 加权幻觉率 | 校验次数 |",
            "| --- | --- | --- | --- |",
        ]
        for i, m in enumerate(s["model_ranking"], 1):
            lines.append(
                f"| {i} | {m['model']} | {m['avg_hallucination_rate']:.1%} | {m['runs']} |"
            )

        lines += [
            "",
            "## 最近 5 次校验",
            "",
            "| 时间 | 主题 | 幻觉率 | 可信度 |",
            "| --- | --- | --- | --- |",
        ]
        for r in s["recent_10"][-5:]:
            lines.append(
                f"| {r.get('time', '')} | {r.get('topic', '')} | "
                f"{r.get('hallucination_rate', 0):.1%} | {r.get('confidence_level', '')} |"
            )
        return "\n".join(lines)
