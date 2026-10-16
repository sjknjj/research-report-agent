"""多数据源管理器（v3.7新增，插件式架构，不设上限）。

功能：
1. 支持配置任意多个数据源（不设上限）
2. 主数据源挂了自动切备数据源（容灾）；``auto_failover=False`` 时只用主数据源
3. 多源交叉验证：同一个数字，多个来源不一致时自动标记“数据存疑”
4. 用户用 ``data_sources`` 配置想用哪些数据源
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..common.logger import get_logger
from .sources import AKShareSource, DataSourceBase, DataSourceError, build_data_source

logger = get_logger("data.manager")


def _env_bool(name: str, default: bool) -> bool:
    """解析布尔型环境变量（1/true/yes/on 视为真）。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


class DataSourceManager:
    """多数据源管理器。"""

    def __init__(
        self,
        sources: Optional[List[DataSourceBase]] = None,
        enable_cross_check: bool = True,
        auto_failover: bool = True,
    ):
        """
        :param sources: 数据源列表，第一个是主，后面是备
        :param enable_cross_check: 是否启用多源交叉验证
        :param auto_failover: 主数据源失败时是否自动切备数据源
        """
        self.sources = list(sources) if sources else [AKShareSource()]
        self.enable_cross_check = enable_cross_check
        self.auto_failover = auto_failover
        self._current_idx = 0  # 当前正在用的数据源索引（容灾切换后“粘”在可用源上）

    @property
    def primary_source(self) -> DataSourceBase:
        return self.sources[0]

    @property
    def source_names(self) -> List[str]:
        return [getattr(s, "name", s.__class__.__name__) for s in self.sources]

    def fetch_with_failover(self, fetch_fn_name: str, **kwargs) -> tuple[Any, str]:
        """
        带容灾地取数：主数据源失败自动切备数据源。

        - ``auto_failover=False``：只尝试主数据源，失败直接抛错；
        - 某个备数据源成功后会“粘”在它上面（``_current_idx``），后续请求直接走它，
          直到它再次失败再继续轮转。

        :return: (数据, 数据源名称)
        """
        total = len(self.sources)
        if not self.auto_failover:
            indices = [0]
        else:
            # 从上次成功的源开始轮转，保证不会漏掉任何数据源
            indices = [(self._current_idx + k) % total for k in range(total)]

        last_exc: Optional[Exception] = None
        for i in indices:
            source = self.sources[i]
            source_name = getattr(source, "name", source.__class__.__name__)
            try:
                fn = getattr(source, fetch_fn_name)
                data = fn(**kwargs)
                if i != self._current_idx:
                    if i == 0:
                        logger.info("主数据源已恢复：%s", source_name)
                    else:
                        logger.warning("主数据源失败，已切到备数据源：%s", source_name)
                    self._current_idx = i
                return data, source_name
            except Exception as exc:  # noqa: BLE001 - 各数据源异常类型繁杂，统一容灾
                last_exc = exc
                logger.warning("数据源 %s 取数失败：%s", source_name, exc)
                continue

        if not self.auto_failover:
            raise DataSourceError(f"主数据源取数失败（未启用容灾切换）：{last_exc}")
        raise DataSourceError(f"所有数据源都取数失败：{last_exc}")

    def cross_check_numbers(self, *datas: Dict[str, Any], threshold: float = 0.01) -> List[str]:
        """
        多源交叉验证：比对多个数据源的关键数字，不一致就返回警告列表。

        :param datas: 多个数据源的清洗后数据（可传任意多个，键名兼容数据层口径）
        :param threshold: 相对误差阈值，超过就算不一致（默认 1%）
        :return: 数据不一致的警告列表
        """
        if not self.enable_cross_check or len(datas) < 2:
            return []

        warnings = []
        # 关键指标：第一个键是数据层标准键名，后面是兼容别名
        key_fields = [
            (("revenue_yuan", "revenue", "revenue_yi"), "营收"),
            (("net_profit_yuan", "net_profit"), "净利润"),
            (("revenue_yoy_pct", "revenue_yoy"), "营收同比"),
            (("net_profit_yoy_pct", "net_profit_yoy"), "净利润同比"),
            (("gross_margin_pct", "gross_margin"), "毛利率"),
            (("net_margin_pct", "net_margin"), "净利率"),
            (("roe_pct", "roe"), "ROE"),
            (("pe", "pe_ratio"), "市盈率"),
            (("pb", "pb_ratio"), "市净率"),
        ]

        for aliases, label in key_fields:
            values: List[tuple] = []
            for i, data in enumerate(datas):
                if not isinstance(data, dict):
                    continue
                raw = None
                for key in aliases:
                    if data.get(key) is not None:
                        raw = data[key]
                        break
                if raw is None:
                    continue
                try:
                    values.append((f"数据源{i + 1}", float(raw)))
                except (TypeError, ValueError):
                    continue

            # 少于两个值就没法比对
            if len(values) < 2:
                continue

            nums = [v[1] for v in values]
            max_val, min_val = max(nums), min(nums)
            # 对称口径：以两端绝对值的均值为分母，避免“小值除以大值”的偏斜
            denom = (abs(max_val) + abs(min_val)) / 2
            if denom == 0:
                continue
            rel_diff = abs(max_val - min_val) / denom

            if rel_diff > threshold:
                values_str = "，".join(f"{name}={val:.2f}" for name, val in values)
                warnings.append(
                    f"⚠️ 数据不一致：{label}（{values_str}）最大最小误差 {rel_diff:.1%}"
                )

        return warnings


def build_manager_from_config(config: Optional[Dict[str, Any]] = None) -> DataSourceManager:
    """
    从配置构建多数据源管理器（插件式，不设上限）。

    配置示例（config.json 里的 data_source 段）：
    ```json
    {
      "data_source": {
        "sources": ["akshare", "tushare"],
        "auto_failover": true,
        "cross_check_enabled": true
      }
    }
    ```
    """
    config = config or {}
    source_names = config.get("sources") or ["akshare"]
    auto_failover = bool(config.get("auto_failover", True))
    cross_check = bool(config.get("cross_check_enabled", True))

    sources: List[DataSourceBase] = []
    for name in source_names:
        try:
            # 不同数据源传不同的参数
            kwargs: Dict[str, Any] = {}
            if name == "tushare":
                kwargs["token"] = os.getenv("TUSHARE_TOKEN", "")
            if name == "mcp":
                # 优先用 config.json 里的 mcp_server_url，其次回退环境变量 MCP_SERVER_URL
                kwargs["server_url"] = (
                    config.get("mcp_server_url") or os.getenv("MCP_SERVER_URL", "")
                )

            source = build_data_source(name, **kwargs)
            sources.append(source)
            logger.info("已加载数据源：%s", name)
        except Exception as exc:  # noqa: BLE001 - 单个数据源失败不阻塞其它
            logger.warning("数据源 %s 加载失败，跳过：%s", name, exc)

    if not sources:
        logger.warning("没有可用的数据源，回退到 AKShare")
        sources = [AKShareSource()]

    return DataSourceManager(
        sources=sources,
        enable_cross_check=cross_check,
        auto_failover=auto_failover,
    )


def build_manager_from_settings(settings: Optional[Any] = None) -> Optional[DataSourceManager]:
    """根据 settings / 环境变量构建多数据源管理器。

    读取顺序（均为可选）：
    - ``settings.data_sources``        → 数据源名称列表；缺省读环境变量 ``DATA_SOURCES``
    - ``settings.cross_check_enabled`` → 环境变量 ``CROSS_CHECK_ENABLED``
    - ``settings.auto_failover``       → 环境变量 ``AUTO_FAILOVER``

    **未配置任何数据源时返回 None**（调用方保持单数据源行为，避免平白多打网络请求）。
    """
    mcp_server_url = getattr(settings, "mcp_server_url", "") if settings is not None else ""

    raw_sources = getattr(settings, "data_sources", None) if settings is not None else None
    if isinstance(raw_sources, str):
        raw_sources = [item.strip() for item in raw_sources.split(",") if item.strip()]
    if not raw_sources:
        env_sources = os.environ.get("DATA_SOURCES", "")
        raw_sources = [item.strip() for item in env_sources.split(",") if item.strip()]
    if not raw_sources:
        return None

    cross_check = getattr(settings, "cross_check_enabled", None) if settings is not None else None
    if cross_check is None:
        cross_check = _env_bool("CROSS_CHECK_ENABLED", True)
    auto_failover = getattr(settings, "auto_failover", None) if settings is not None else None
    if auto_failover is None:
        auto_failover = _env_bool("AUTO_FAILOVER", True)

    return build_manager_from_config(
        {
            "sources": list(raw_sources),
            "cross_check_enabled": bool(cross_check),
            "auto_failover": bool(auto_failover),
            "mcp_server_url": mcp_server_url,
        }
    )
