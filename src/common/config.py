"""配置加载。

配置优先级（后者覆盖前者）：
1. 内置默认值
2. config.json（若存在）
3. .env 文件（若存在）
4. 真实环境变量
5. CLI 显式传入的参数（在 main.py 中处理）

为保持“核心零第三方依赖”，这里自行实现极简 .env 解析，
不依赖 python-dotenv / PyYAML。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(dotenv_path: Optional[Path] = None) -> None:
    """把 .env 中的键值对写入 os.environ（不覆盖已存在的真实环境变量）。"""
    path = dotenv_path or (PROJECT_ROOT / ".env")
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        # 去掉成对的单/双引号
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_list(key: str, default: List[str]) -> List[str]:
    raw = os.environ.get(key)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    """运行配置。"""

    # ---- 大模型 ----
    mode: str = "auto"                       # auto / online / offline
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    temperature: float = 0.3
    max_tokens: int = 2048
    request_timeout: int = 60

    # ---- 检索工具 ----
    search_provider: str = "auto"            # auto / duckduckgo / tavily / mock
    tavily_api_key: str = ""
    max_results_per_query: int = 5
    max_fetch_pages: int = 3                 # 每个主题最多抓取多少个网页正文
    fetch_content_chars: int = 2000          # 单个网页正文截断长度

    # ---- 报告与流程 ----
    language: str = "zh"
    max_review_rounds: int = 1
    deep_search_rounds: int = 2              # 深挖模式下的补充检索轮数
    output_dir: str = "output"
    export_formats: List[str] = field(default_factory=lambda: ["md", "html"])
    log_level: str = "INFO"

    # ---- 运行期解析 ----
    effective_mode: str = "offline"          # 经过 auto 判定后的真实模式

    @property
    def is_offline(self) -> bool:
        return self.effective_mode == "offline"

    def resolve_effective_mode(self) -> str:
        """根据 auto 规则判定最终运行模式。"""
        if self.mode == "offline":
            self.effective_mode = "offline"
        elif self.mode == "online":
            # 显式要求在线但没有 key，则保持 online，由调用方报错提示
            self.effective_mode = "online"
        else:  # auto
            self.effective_mode = "online" if self.llm_api_key else "offline"
        return self.effective_mode

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.llm_api_key:
            data["llm_api_key"] = "***masked***"
        return data


def load_settings(
    config_path: Optional[str] = None,
    cli_overrides: Optional[Dict[str, Any]] = None,
) -> Settings:
    """加载配置并完成环境变量合并与模式判定。

    :param config_path: 自定义 config.json 路径；为 None 时读取项目根下的 config.json
    :param cli_overrides: CLI 显式覆盖项（最高优先级）
    """
    load_dotenv()

    file_config: Dict[str, Any] = {}
    cfg_path = Path(config_path) if config_path else (PROJECT_ROOT / "config.json")
    if cfg_path.exists():
        try:
            file_config = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            file_config = {}

    settings = Settings()

    # 1) 配置文件
    for key, value in file_config.items():
        if hasattr(settings, key) and value is not None:
            setattr(settings, key, value)

    # 2) 环境变量
    settings.mode = _env_str("LLM_MODE", settings.mode)
    settings.llm_base_url = _env_str("LLM_BASE_URL", settings.llm_base_url)
    settings.llm_api_key = _env_str("LLM_API_KEY", settings.llm_api_key)
    settings.llm_model = _env_str("LLM_MODEL", settings.llm_model)
    settings.temperature = _env_float("LLM_TEMPERATURE", settings.temperature)
    settings.max_tokens = _env_int("LLM_MAX_TOKENS", settings.max_tokens)
    settings.request_timeout = _env_int("LLM_TIMEOUT", settings.request_timeout)
    settings.search_provider = _env_str("SEARCH_PROVIDER", settings.search_provider)
    settings.tavily_api_key = _env_str("TAVILY_API_KEY", settings.tavily_api_key)
    settings.max_results_per_query = _env_int(
        "SEARCH_MAX_RESULTS", settings.max_results_per_query
    )
    settings.max_fetch_pages = _env_int("SEARCH_MAX_FETCH", settings.max_fetch_pages)
    settings.fetch_content_chars = _env_int("FETCH_CHARS", settings.fetch_content_chars)
    settings.language = _env_str("REPORT_LANGUAGE", settings.language)
    settings.max_review_rounds = _env_int("REVIEW_MAX_ROUNDS", settings.max_review_rounds)
    settings.deep_search_rounds = _env_int("DEEP_SEARCH_ROUNDS", settings.deep_search_rounds)
    settings.output_dir = _env_str("OUTPUT_DIR", settings.output_dir)
    if os.environ.get("EXPORT_FORMATS"):
        settings.export_formats = _env_list("EXPORT_FORMATS", settings.export_formats)
    settings.log_level = _env_str("LOG_LEVEL", settings.log_level)

    # 3) CLI 覆盖（最高优先级）
    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is not None and hasattr(settings, key):
                setattr(settings, key, value)

    settings.resolve_effective_mode()
    return settings


# -- 模块级单例缓存（Web/MCP 等常驻进程只需要加载一次） ----------------
_cached_settings: Optional[Settings] = None


def get_settings(cli_overrides: Optional[Dict[str, Any]] = None) -> Settings:
    """获取配置单例：第一次调用时加载 .env / config.json，之后直接返回缓存。

    CLI 入口请用 ``load_settings()`` 以确保每次都重新读环境变量；
    Web / MCP / 常驻服务请用 ``get_settings()`` 以避免重复读盘。
    """
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = load_settings(cli_overrides=cli_overrides)
    return _cached_settings
