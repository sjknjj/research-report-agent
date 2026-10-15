"""情绪面分析。

用大模型分析新闻的情绪倾向（正面/负面/中性），给出综合情绪评分
（0-100，越高越乐观），并**统一规范化输出结构**供 Web/CLI 直接使用。

设计原则：
- 无论 LLM 返回什么（非 dict、缺字段、数字带单位、字符串 "false"），
  本函数都返回结构固定的 dict，绝不把异常/字符串透传给上层。
- 离线模式下也能给出确定性结果（不编造情绪，明确标注为"未分析"）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..llm.client import LLMClient

logger = logging.getLogger("analysis.sentiment")

_MAX_NEWS = 10
_NEUTRAL_SCORE = 50.0


def _to_int(value: Any, default: int = 0) -> int:
    """把 LLM 可能返回的 '3'、'3条'、3.0、None 都安全转成 int。"""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    try:
        text = str(value).strip()
        digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
        return int(float(digits)) if digits else default
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    """把 LLM 可能返回的 '72分'、'72.5'、None 安全转成 float。"""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    try:
        text = str(value)
        cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
        return float(cleaned) if cleaned not in ("", "-", ".") else default
    except (TypeError, ValueError):
        return default


def _empty_result(summary: str, news_count: int = 0) -> Dict[str, Any]:
    """无新闻/未分析时的中性结果（结构完整）。"""
    return {
        "overall": "中性",
        "score": _NEUTRAL_SCORE,
        "positive": 0,
        "negative": 0,
        "neutral": news_count,
        "positive_ratio": 0.0,
        "negative_ratio": 0.0,
        "neutral_ratio": 1.0 if news_count else 0.0,
        "positive_news": [],
        "negative_news": [],
        "summary": summary,
        "analyzed": False,
    }


def _overall_from(score: float, positive: int, negative: int) -> str:
    if positive > negative:
        return "正面"
    if negative > positive:
        return "负面"
    if score >= 60:
        return "正面"
    if score <= 40:
        return "负面"
    return "中性"


def analyze_sentiment(
    news_list: List[Dict[str, Any]], llm: LLMClient
) -> Dict[str, Any]:
    """分析新闻情绪。

    :param news_list: 新闻列表，每个元素含 title / content（content 可选）
    :param llm: 大模型客户端
    :return: 固定结构 dict，键见模块 docstring / 契约文件
    """
    if not news_list:
        return _empty_result("暂无新闻数据，无法分析情绪", 0)

    top_news = [n for n in news_list[:_MAX_NEWS] if isinstance(n, dict)]
    if not top_news:
        return _empty_result("新闻数据格式异常，无法分析情绪", 0)

    # 同时带上标题与正文摘要，正文往往比标题更能反映情绪
    lines = []
    for idx, item in enumerate(top_news, 1):
        title = str(item.get("title", "") or "").strip()
        content = str(item.get("content", "") or "").strip().replace("\n", " ")[:120]
        lines.append(f"{idx}. {title}" + (f"（摘要：{content}）" if content else ""))
    news_text = "\n".join(lines)

    prompt = f"""
请分析以下股票新闻的情绪倾向：

{news_text}

请输出 JSON 格式：
{{
  "positive": 正面新闻数量,
  "negative": 负面新闻数量,
  "neutral": 中性新闻数量,
  "score": 综合情绪评分（0-100，越高越乐观）,
  "summary": 一句话总结（不超过50字）
}}
"""

    parsed: Optional[Dict[str, Any]] = None
    failure_reason = ""
    try:
        result = llm.chat_json(
            messages=[{"role": "user", "content": prompt}],
            stage="sentiment_analysis",
            temperature=0.2,
        )
        if isinstance(result, dict):
            parsed = result
        else:
            # 离线模式或模型直接返回字符串时的兜底
            failure_reason = "模型未返回结构化结果（可能处于离线演示模式）"
    except Exception as exc:  # noqa: BLE001
        failure_reason = f"情绪分析调用失败：{exc}"
        logger.warning("情绪分析失败：%s", exc)

    if parsed is None:
        result_out = _empty_result(failure_reason or "情绪分析未获得结果", len(top_news))
        result_out["news_count"] = len(top_news)
        return result_out

    positive = max(0, _to_int(parsed.get("positive"), 0))
    negative = max(0, _to_int(parsed.get("negative"), 0))
    neutral = max(0, _to_int(parsed.get("neutral"), 0))
    total = positive + negative + neutral
    if total == 0:
        # 模型没给计数：按总数记为中性，避免出现 0/0
        neutral = len(top_news)
        total = neutral

    score = max(0.0, min(100.0, _to_float(parsed.get("score"), _NEUTRAL_SCORE)))

    # 部分模型会返回逐条新闻的情绪标注，有就用来填充"主要正面/负面新闻"
    def _pick_news(key_names, fallback_positive: bool) -> List[Dict[str, Any]]:
        for key in key_names:
            items = parsed.get(key)
            if isinstance(items, list) and items:
                out = []
                for it in items[:5]:
                    if isinstance(it, dict):
                        out.append({"title": str(it.get("title", "") or ""), "reason": str(it.get("reason", "") or "")})
                    else:
                        out.append({"title": str(it), "reason": ""})
                return out
        return []

    positive_news = _pick_news(("positive_news", "positive_list"), True)
    negative_news = _pick_news(("negative_news", "negative_list"), False)

    result = {
        "overall": _overall_from(score, positive, negative),
        "score": round(score, 1),
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "positive_ratio": round(positive / total, 4) if total else 0.0,
        "negative_ratio": round(negative / total, 4) if total else 0.0,
        "neutral_ratio": round(neutral / total, 4) if total else 0.0,
        "positive_news": positive_news,
        "negative_news": negative_news,
        "summary": str(parsed.get("summary", "") or "").strip() or "模型未给出总结",
        "analyzed": True,
        "news_count": len(top_news),
    }
    return result
