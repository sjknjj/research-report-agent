"""数据缓存（v3.3新增）：同一天同一只股票的数据直接复用，避免重复抓取。

缓存策略：
- 按日期分目录：``cache/2026-10-15/600519_20250101_20251231_qfq.json``
- **缓存键包含“股票代码 + 起止日期 + 复权方式”**，不同区间/复权互不串味
- **清洗后的行情/财务 DataFrame 一并持久化**，命中后可直接重建，
  避免 K 线图 / 财务趋势图因缓存只存 summary 而静默消失
- 同一天内同一只股票重复请求，直接读缓存，不重新抓
- 跨天自动失效，重新抓取
- 旧版本 / 残缺 / 结构异常的缓存一律视为“未命中”，绝不向上抛异常
- 离线模式下不启用缓存
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from ..common.config import PROJECT_ROOT
from ..common.logger import get_logger

logger = get_logger("data.cache")

CACHE_DIR = PROJECT_ROOT / "cache"

# 缓存结构版本：结构变化时旧缓存自动失效，避免硬索引 KeyError
CACHE_VERSION = 2

# 需要按日期还原的列名（与 cleaner 的列名约定保持一致）
_DATE_COLUMNS = {"date", "period"}

# 缓存文件里必须存在的顶层键（缺一即视为坏缓存）
_REQUIRED_KEYS = (
    "symbol", "name", "source", "as_of",
    "price_summary", "financial_summary",
)


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _sanitize(text: Any) -> str:
    """把日期 / 复权等参数安全地拼进文件名（去掉路径分隔符等）。"""
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in str(text))
    cleaned = cleaned.strip("-")
    return cleaned or "na"


def _cache_key(symbol: str, start_date: str, end_date: str, adjust: str) -> str:
    """缓存键：股票代码 + 起止日期 + 复权方式。"""
    return (
        f"{_sanitize(symbol)}_{_sanitize(start_date)}_"
        f"{_sanitize(end_date)}_{_sanitize(adjust)}"
    )


def _cache_path(symbol: str, start_date: str, end_date: str, adjust: str) -> Path:
    return CACHE_DIR / _today_str() / f"{_cache_key(symbol, start_date, end_date, adjust)}.json"


# ---------------------------------------------------------------------------
# DataFrame <-> JSON 互转（保留列类型，命中后可直接用于绘图/分析）
# ---------------------------------------------------------------------------
def _json_default(obj: Any):
    """json.dumps 的兜底序列化：numpy / pandas 标量 → 原生 Python。"""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        value = float(obj)
        return None if value != value else value  # NaN → null
    if isinstance(obj, np.bool_):
        return bool(obj)
    if obj is pd.NaT:
        return None
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    return str(obj)


def _df_to_payload(df: Optional[pd.DataFrame]) -> Optional[Dict[str, Any]]:
    """把 DataFrame 序列化为可 JSON 化的结构（记录列名与 dtype）。"""
    if df is None or getattr(df, "empty", True):
        return None
    work = df.copy()
    dtypes: Dict[str, str] = {}
    for col in work.columns:
        dtypes[str(col)] = str(work[col].dtype)
        if pd.api.types.is_datetime64_any_dtype(work[col]):
            work[col] = work[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "columns": [str(c) for c in work.columns],
        "dtypes": dtypes,
        "records": work.to_dict(orient="records"),
    }


def _df_from_payload(payload: Any) -> Optional[pd.DataFrame]:
    """从缓存结构重建 DataFrame，并还原日期/数值列类型。"""
    if not isinstance(payload, dict):
        return None
    columns = payload.get("columns")
    records = payload.get("records")
    if not columns or not records:
        return None
    df = pd.DataFrame(records, columns=[str(c) for c in columns])
    dtypes = payload.get("dtypes") or {}
    for col in df.columns:
        dtype = str(dtypes.get(col, ""))
        if col in _DATE_COLUMNS or "datetime" in dtype:
            df[col] = pd.to_datetime(df[col], errors="coerce")
        elif any(flag in dtype for flag in ("int", "float")):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
def get_cached_bundle(
    symbol: str,
    start_date: str = "",
    end_date: str = "",
    adjust: str = "qfq",
) -> Optional[Dict[str, Any]]:
    """读取当天、指定区间/复权的缓存数据；没有或结构异常则返回 None。

    返回的字典里 ``price_clean`` / ``financial_clean`` 已被重建为 DataFrame。
    """
    path = _cache_path(symbol, start_date, end_date, adjust)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("缓存读取失败（按未命中处理）：%s", exc)
        return None

    if not _is_valid_payload(data):
        logger.warning("缓存结构不完整（按未命中处理）：%s", path)
        return None

    try:
        price_clean = _df_from_payload(data.get("price_clean"))
        financial_clean = _df_from_payload(data.get("financial_clean"))
    except Exception as exc:  # noqa: BLE001 - 坏缓存不能打断主流程
        logger.warning("缓存重建 DataFrame 失败（按未命中处理）：%s", exc)
        return None

    if price_clean is None or price_clean.empty:
        logger.info("缓存缺少行情清洗帧，按未命中处理：%s", path)
        return None
    if financial_clean is None or financial_clean.empty:
        logger.info("缓存缺少财务清洗帧，按未命中处理：%s", path)
        return None

    data["price_clean"] = price_clean
    data["financial_clean"] = financial_clean
    logger.info("缓存命中：%s（%s）", symbol, path)
    return data


def _is_valid_payload(data: Any) -> bool:
    """校验缓存结构，防止旧/残缺缓存导致 KeyError。"""
    if not isinstance(data, dict):
        return False
    if data.get("version") != CACHE_VERSION:
        return False
    for key in _REQUIRED_KEYS:
        if key not in data:
            return False
    for key in ("info", "price_summary", "financial_summary", "valuation", "fund_flow", "technicals"):
        if key in data and data[key] is not None and not isinstance(data[key], (dict, list)):
            return False
    if data.get("news") is not None and not isinstance(data["news"], list):
        return False
    return True


def save_cached_bundle(
    symbol: str,
    bundle_dict: Dict[str, Any],
    start_date: str = "",
    end_date: str = "",
    adjust: str = "qfq",
    price_clean: Optional[pd.DataFrame] = None,
    financial_clean: Optional[pd.DataFrame] = None,
) -> None:
    """把数据包（含清洗后的 DataFrame）写入当天、指定区间/复权的缓存。"""
    path = _cache_path(symbol, start_date, end_date, adjust)
    payload: Dict[str, Any] = dict(bundle_dict or {})
    payload["version"] = CACHE_VERSION
    payload["price_clean"] = _df_to_payload(price_clean)
    payload["financial_clean"] = _df_to_payload(financial_clean)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                payload, ensure_ascii=False, indent=2, default=_json_default,
                allow_nan=True,
            ),
            encoding="utf-8",
        )
        logger.info("已写入缓存：%s", path)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("缓存写入失败：%s", exc)


def clear_expired_cache(days: int = 7) -> int:
    """清理 N 天前的缓存目录，返回删除的目录数。

    仅按目录 mtime 判断，今天的缓存目录不会被删除。
    """
    if not CACHE_DIR.exists():
        return 0
    cutoff = datetime.now().timestamp() - days * 86400
    count = 0
    try:
        entries = list(CACHE_DIR.iterdir())
    except OSError as exc:
        logger.warning("缓存目录遍历失败：%s", exc)
        return 0
    for entry in entries:
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                count += 1
        except OSError as exc:  # noqa: PERF203 - 单个目录失败不影响其它
            logger.debug("清理缓存目录 %s 失败：%s", entry, exc)
    return count
