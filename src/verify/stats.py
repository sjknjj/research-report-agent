"""幻觉率历史统计（3.5新增）。

每次校验完自动存一条记录，用来：
1. 看某只股票/某个模型的历史幻觉率
2. 对比不同模型哪个更靠谱
3. 发现高风险主题（幻觉率特别高的）
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


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
                return json.load(f)
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
        """生成统计摘要。"""
        if not self.records:
            return {"total": 0, "message": "暂无历史记录"}

        total = len(self.records)
        avg_rate = sum(r["hallucination_rate"] for r in self.records) / total

        # 按模型分组统计
        by_model: Dict[str, List[float]] = {}
        for r in self.records:
            model = r["model"]
            by_model.setdefault(model, []).append(r["hallucination_rate"])

        model_ranking = []
        for model, rates in by_model.items():
            model_ranking.append({
                "model": model,
                "runs": len(rates),
                "avg_hallucination_rate": round(sum(rates) / len(rates), 4),
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
        if s.get("total", 0) == 0:
            return "暂无历史幻觉率记录。跑几份报告后再来看看吧。"

        lines = [
            f"📊 幻觉率历史统计（共 {s['total_runs']} 次）",
            f"平均幻觉率：{s['avg_hallucination_rate']:.1%}",
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
