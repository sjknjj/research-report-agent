"""数据缓存（v3.3新增）：同一天同一只股票的数据直接复用，避免重复抓取。

缓存策略：
- 按日期分目录：cache/2026-10-15/600519.json
- 同一天内同一只股票重复请求，直接读缓存，不重新抓
- 跨天自动失效，重新抓取
- 离线模式下不启用缓存
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..common.config import PROJECT_ROOT
from ..common.logger import get_logger

logger = get_logger("data.cache")

CACHE_DIR = PROJECT_ROOT / "cache"


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / _today_str() / f"{symbol}.json"


def get_cached_bundle(symbol: str) -> Optional[Dict[str, Any]]:
    """读取当天的缓存数据，没有则返回 None。"""
    path = _cache_path(symbol)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.info("缓存命中：%s（%s）", symbol, path)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("缓存读取失败：%s", exc)
        return None


def save_cached_bundle(symbol: str, bundle_dict: Dict[str, Any]) -> None:
    """把数据包写入当天的缓存。"""
    path = _cache_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps(bundle_dict, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("已写入缓存：%s", path)
    except OSError as exc:
        logger.warning("缓存写入失败：%s", exc)


def clear_expired_cache(days: int = 7) -> int:
    """清理 N 天前的缓存，返回删除的目录数。"""
    if not CACHE_DIR.exists():
        return 0
    cutoff = datetime.now().timestamp() - days * 86400
    count = 0
    for d in CACHE_DIR.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            count += 1
    return count
