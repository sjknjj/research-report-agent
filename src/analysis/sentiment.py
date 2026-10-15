"""情绪面分析（简化版）。

用大模型分析新闻的情绪倾向（正面/负面/中性），
然后给出综合情绪评分（0-100，越高越乐观）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..llm.client import LLMClient

logger = logging.getLogger("analysis.sentiment")


def analyze_sentiment(news_list: List[Dict[str, Any]], llm: LLMClient) -> Dict[str, Any]:
    """分析新闻情绪。

    :param news_list: 新闻列表，每个元素有 title / content
    :param llm: 大模型客户端
    :return: {score, positive, negative, neutral, summary}
    """
    if not news_list:
        return {"score": 50, "positive": 0, "negative": 0, "neutral": 0, "summary": "无新闻"}

    # 取前 10 条新闻
    top_news = news_list[:10]
    news_text = "\n".join(f"{i+1}. {n.get('title', '')}" for i, n in enumerate(top_news))

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

    try:
        result = llm.chat_json(
            messages=[{"role": "user", "content": prompt}],
            stage="sentiment_analysis",
            temperature=0.2,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("情绪分析失败：%s", exc)
        return {"score": 50, "positive": 0, "negative": 0, "neutral": len(news_list), "summary": "情绪分析失败"}
