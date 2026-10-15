"""轻量级日志工具。

仅封装标准库 logging，提供统一的格式与获取入口，避免各模块重复配置。

设计约定：
1. ``setup_logging`` 只应由顶层 CLI 入口（main.py）显式调用，
   ``get_logger`` 不再隐式触发配置，避免破坏宿主（uvicorn / FastAPI / pytest）
   已经安装的日志 handler。
2. 日志一律输出到 **stderr**，这样 ``python main.py brief ... > report.md``
   重定向 stdout 时不会把日志行混进报告正文。
3. 不清空已有 handler：宿主若已配置日志，则沿用其 handler。
"""
from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def setup_logging(level: str = "INFO", stream=None) -> None:
    """配置根日志器（幂等）。

    - 只在根日志器没有任何 handler 时才新建一个 stderr handler；
    - 绝不清空宿主已有的 handler；
    - 重复调用只会更新日志级别。
    """
    root = logging.getLogger()
    target = stream if stream is not None else sys.stderr

    if not root.handlers:
        handler = logging.StreamHandler(target)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)

    try:
        root.setLevel(level.upper())
    except (AttributeError, ValueError):
        root.setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """获取命名日志器。

    不再隐式调用 :func:`setup_logging`：未配置时交给 Python 默认行为
    （WARNING 及以上走 lastResort handler），避免污染宿主日志配置。
    """
    return logging.getLogger(name)
