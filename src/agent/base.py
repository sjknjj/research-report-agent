"""智能体基类与公共辅助。"""
from __future__ import annotations

import re
from typing import Any, Optional

from ..common.logger import get_logger
from ..common.schema import AgentContext
from ..llm.client import LLMError
from ..tools.search import SearchTool

# 认证 / 配置类错误的特征串：这类错误不能降级成占位文本，必须上抛，
# 否则 API Key 无效时流水线依然会“成功”，产出一份看似可信的假报告。
_FATAL_LLM_HINTS = (
    "401",
    "403",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "authentication",
    "missing api key",
    "llm_api_key",
    "api key",
)

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_TRUE_WORDS = {"true", "1", "yes", "y", "on", "是", "通过", "正确", "ok", "success"}
_FALSE_WORDS = {"false", "0", "no", "n", "off", "否", "不通过", "未通过", "失败", "错误", "none", "null", ""}


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

    # -- 稳健的 LLM JSON 取值（模型经常返回 "7.5" / "85分" / "false" / null）----
    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        """把任意模型输出转为 int，取出其中的第一个数字；失败返回 default。"""
        if isinstance(value, bool):
            return int(value)
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return int(round(value))
        match = _NUMBER_RE.search(str(value))
        if not match:
            return default
        try:
            return int(round(float(match.group())))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        """把任意模型输出转为 float；失败返回 default。"""
        if isinstance(value, bool):
            return float(value)
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        match = _NUMBER_RE.search(str(value))
        if not match:
            return default
        try:
            return float(match.group())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool(value: Any, default: bool) -> bool:
        """把任意模型输出转为 bool；``"false"`` 必须解析为 False。"""
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        return default

    @staticmethod
    def _to_str_list(value: Any) -> list:
        """把模型返回的列表/字符串统一成非空字符串列表。"""
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    # -- 错误分级 -----------------------------------------------------------
    @staticmethod
    def _is_fatal_llm_error(exc: BaseException) -> bool:
        """判断异常是否属于认证/配置类致命错误（应上抛而非兜底）。"""
        text = str(exc).lower()
        return any(hint in text for hint in _FATAL_LLM_HINTS)

    def _raise_if_fatal(self, exc: BaseException) -> None:
        """认证/配置类错误直接上抛，避免静默降级。"""
        if self._is_fatal_llm_error(exc):
            raise exc

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
        """统一的 LLM JSON 调用：瞬时失败返回兜底，认证/配置错误上抛。"""
        who = f"「{label}」" if label else ""
        try:
            result = self.llm.chat_json(
                messages, stage=stage, context=context, temperature=temperature,
            )
            # 离线模式下可能返回字符串而非 dict，统一兜底
            if not isinstance(result, dict):
                raise ValueError(f"预期 JSON 对象，实际返回 {type(result).__name__}")
            return result
        except LLMError as exc:
            if self._is_fatal_llm_error(exc):
                self.logger.error("%s%s 认证/配置错误，中止流水线：%s", who, stage, exc)
                raise
            self.logger.warning("%s%s 调用失败，使用兜底数据：%s", who, stage, exc)
            return fallback
        except (AttributeError, ValueError, TypeError) as exc:
            self.logger.warning("%s%s 结果异常，使用兜底数据：%s", who, stage, exc)
            return fallback

    def _safe_chat(
        self,
        messages: list,
        fallback: str,
        stage: str,
        context: Optional[dict] = None,
        temperature: float = 0.5,
    ) -> str:
        """统一的 LLM 文本调用：瞬时失败返回兜底，认证/配置错误上抛。"""
        try:
            return self.llm.chat(messages, stage=stage, context=context, temperature=temperature)
        except LLMError as exc:
            if self._is_fatal_llm_error(exc):
                self.logger.error("%s 认证/配置错误，中止流水线：%s", stage, exc)
                raise
            self.logger.warning("%s 调用失败，使用兜底文本：%s", stage, exc)
            return fallback
        except (AttributeError, ValueError, TypeError) as exc:
            self.logger.warning("%s 结果异常，使用兜底文本：%s", stage, exc)
            return fallback
