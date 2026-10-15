"""轻量级日志工具。

仅封装标准库 logging，提供统一的格式与获取入口，避免各模块重复配置。
"""
from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s"
_DATE_FORMAT = "%H:%M:%S"
_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    """初始化根日志器，重复调用是安全的（幂等）。"""
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """获取命名日志器。"""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)
