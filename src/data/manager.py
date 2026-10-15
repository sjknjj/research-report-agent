"""多数据源管理器（v3.7新增）。

功能：
1. 支持配置多个数据源（主 + 备）
2. 主数据源挂了自动切备数据源（容灾）
3. 多源交叉验证：同一个数字，多个来源不一致时自动标记"数据存疑"
4. 用户在 config.yaml 里选想用哪些数据源
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..common.logger import get_logger
from .sources import AKShareSource, DataSourceError

logger = get_logger("data.manager")


class DataSourceManager:
    """多数据源管理器。"""

    def __init__(self, sources: Optional[List[Any]] = None, enable_cross_check: bool = True):
        """
        :param sources: 数据源列表，第一个是主，后面是备
        :param enable_cross_check: 是否启用多源交叉验证
        """
        self.sources = sources or [AKShareSource()]
        self.enable_cross_check = enable_cross_check
        self._current_idx = 0  # 当前正在用的数据源索引

    @property
    def primary_source(self):
        return self.sources[0]

    def fetch_with_failover(self, fetch_fn_name: str, **kwargs) -> tuple[Any, str]:
        """
        带容灾地取数：主数据源失败自动切备数据源。

        :return: (数据, 数据源名称)
        """
        last_exc: Optional[Exception] = None
        for i, source in enumerate(self.sources):
            try:
                fn = getattr(source, fetch_fn_name)
                data = fn(**kwargs)
                source_name = source.__class__.__name__
                if i > 0:
                    logger.warning("主数据源失败，已切到备数据源：%s", source_name)
                return data, source_name
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("数据源 %s 取数失败：%s", source.__class__.__name__, exc)
                continue

        raise DataSourceError(f"所有数据源都取数失败：{last_exc}")

    def cross_check_numbers(self, data_a: Dict[str, Any], data_b: Dict[str, Any]) -> List[str]:
        """
        多源交叉验证：比对两个数据源的关键数字，不一致就返回警告列表。

        :param data_a: 第一个数据源的清洗后数据
        :param data_b: 第二个数据源的清洗后数据
        :return: 数据不一致的警告列表
        """
        if not self.enable_cross_check or not data_a or not data_b:
            return []

        warnings = []
        # 比对几个关键指标（相对误差 > 1% 就算不一致）
        key_fields = [
            ("revenue", "营收"),
            ("net_profit", "净利润"),
            ("pe_ratio", "市盈率"),
            ("pb_ratio", "市净率"),
        ]

        for key, label in key_fields:
            val_a = data_a.get(key)
            val_b = data_b.get(key)
            if val_a is None or val_b is None:
                continue
            try:
                a, b = float(val_a), float(val_b)
                if a == 0 or b == 0:
                    continue
                rel_diff = abs(a - b) / max(abs(a), abs(b))
                if rel_diff > 0.01:  # 误差 > 1%
                    warnings.append(
                        f"⚠️ 数据不一致：{label} 数据源A={a:.2f}，数据源B={b:.2f}（误差 {rel_diff:.1%}）"
                    )
            except (TypeError, ValueError):
                continue

        return warnings
