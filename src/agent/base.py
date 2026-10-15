"""智能体基类与公共辅助。"""
from __future__ import annotations

from typing import Any, Optional

from ..common.logger import get_logger
from ..common.schema import AgentContext
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
