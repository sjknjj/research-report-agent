"""智能体基类与公共辅助。"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..common.logger import get_logger
from ..common.schema import AgentContext
from ..llm.client import LLMError
from ..tools.search import SearchTool


class BaseAgent:
    """所有智能体的抽象基类。"""

    role_name: str = "base"

    def __init__(self, llm: Any, settings: Any):
        self.llm = llm
        self.settings = settings
        self.logger = get_logger(f"agent.{self.role_name}")

    def run(self, ctx: AgentContext, search_tool: Optional[SearchTool] = None) -> None:
        """执行该阶段，就地修改共享上下文 ``ctx``。"""
        raise NotImplementedError

    @staticmethod
    def _user(content: str) -> dict:
        return {"role": "user", "content": content}

    @staticmethod
    def _system(content: str) -> dict:
        return {"role": "system", "content": content}

    def _safe_chat_json(
        self,
        messages: list,
        fallback: dict,
        stage: str,
        context: Optional[dict] = None,
        temperature: float = 0.3,
        label: str = "",
    ) -> dict:
        """统一的 LLM JSON 调用：失败自动返回兜底数据，避免每个 agent 手写 try/except。"""
        try:
            result = self.llm.chat_json(
                messages, stage=stage, context=context, temperature=temperature,
            )
            # 离线模式下可能返回字符串而非 dict，统一兜底
            if not isinstance(result, dict):
                raise ValueError(f"预期 JSON 对象，实际返回 {type(result).__name__}")
            return result
        except (LLMError, AttributeError, ValueError, TypeError) as exc:
            who = f"「{label}」" if label else ""
            self.logger.warning("%s%s 失败，使用兜底数据：%s", who, stage, exc)
            return fallback

    def _safe_chat(
        self,
        messages: list,
        fallback: str,
        stage: str,
        context: Optional[dict] = None,
        temperature: float = 0.5,
    ) -> str:
        """统一的 LLM 文本调用：失败自动返回兜底文本。"""
        try:
            return self.llm.chat(messages, stage=stage, context=context, temperature=temperature)
        except (LLMError, AttributeError, ValueError, TypeError) as exc:
            self.logger.warning("%s 失败，使用兜底文本：%s", stage, exc)
            return fallback
