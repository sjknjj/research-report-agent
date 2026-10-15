"""AKShare 数据抓取适配层。

职责：
- 懒加载 akshare（未安装时给出可操作的安装提示，而不是裸 ImportError）；
- 对东财/同花顺等接口常见的瞬时断连做有限次重试；
- 统一异常为 :class:`DataSourceError`，上层据此回退到离线样例；
- 只负责“取数”，清洗一律交给 :mod:`src.data.cleaner`。

注意：AKShare 接口与字段随版本变化，这里对每个接口都做了防御式判断。
"""
from __future__ import annotations

import time
from typing import Any, List, Optional

import pandas as pd

from ..common.logger import get_logger

logger = get_logger("data.sources")


class DataSourceError(RuntimeError):
    """数据抓取异常。"""


class DataSourceBase:
    """所有数据源的抽象基类（v3.7新增，插件式架构）。

    想加新数据源只要：
    1. 继承这个基类
    2. 实现 price_daily / financial_abstract / stock_info 三个方法
    3. 在 config.yaml 里配置启用
    """

    name: str = "base"  # 数据源名称，用于配置和交叉验证

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        """获取日线行情。返回 DataFrame。"""
        raise NotImplementedError

    def financial_abstract(self, symbol: str):
        """获取财务摘要。返回 DataFrame。"""
        raise NotImplementedError

    def stock_info(self, symbol: str):
        """获取个股基本信息。返回 DataFrame。"""
        raise NotImplementedError


class AKShareSource(DataSourceBase):
    """AKShare 实时数据来源（东财+同花顺多接口）。"""

    name = "akshare"

    def __init__(self, retry: int = 3, backoff: float = 1.5):
        self.retry = retry
        self.backoff = backoff
        self._ak = None

    # -- 依赖与重试 ---------------------------------------------------------
    @property
    def ak(self):  # noqa: ANN201 - akshare 无精确类型
        if self._ak is None:
            try:
                import akshare as ak  # noqa: PLC0415
            except ImportError as exc:
                raise DataSourceError(
                    "未安装 akshare，请先执行：pip install akshare pandas"
                ) from exc
            self._ak = ak
        return self._ak

    def _call(self, func_name: str, **kwargs) -> pd.DataFrame:
        """带重试地调用 akshare 接口函数。"""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retry + 1):
            try:
                func = getattr(self.ak, func_name)
                df = func(**kwargs)
                if df is None or (hasattr(df, "empty") and df.empty):
                    raise DataSourceError(f"接口 {func_name} 返回空数据")
                return df
            except DataSourceError:
                raise
            except Exception as exc:  # noqa: BLE001 - akshare 异常类型繁杂，统一兜底
                last_exc = exc
                if attempt < self.retry:
                    wait = self.backoff * attempt
                    logger.info(
                        "接口 %s 第 %d 次调用失败（%s），%.1fs 后重试",
                        func_name, attempt, type(exc).__name__, wait,
                    )
                    time.sleep(wait)
        raise DataSourceError(f"接口 {func_name} 重试 {self.retry} 次仍失败：{last_exc}")

    # -- 行情 ---------------------------------------------------------------
    def price_daily(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """A 股日线行情（前复权）。日期格式 YYYYMMDD。"""
        return self._call(
            "stock_zh_a_hist",
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )

    # -- 财务 ---------------------------------------------------------------
    def financial_abstract(self, symbol: str) -> pd.DataFrame:
        """同花顺·财务摘要（按报告期，中文单位）。"""
        return self._call(
            "stock_financial_abstract_ths",
            symbol=symbol,
            indicator="按报告期",
        )

    def stock_info(self, symbol: str) -> pd.DataFrame:
        """东财·个股信息（两列长表）。"""
        return self._call("stock_individual_info_em", symbol=symbol)

    # -- 资讯 ---------------------------------------------------------------
    def company_news(self, symbol: str, limit: int = 10) -> pd.DataFrame:
        """东财·个股相关新闻/公告资讯。"""
        df = self._call("stock_news_em", symbol=symbol)
        return df.head(limit)

    # -- 行业（best-effort，东财限流时允许失败）-----------------------------
    def industry_list(self) -> pd.DataFrame:
        """东财·行业板块列表。"""
        return self._call("stock_board_industry_name_em")

    def industry_constituents(self, industry_name: str) -> pd.DataFrame:
        """东财·指定行业板块成分股。"""
        return self._call("stock_board_industry_cons_em", symbol=industry_name)

    # -- A 股特色数据（参考 TradingAgents-Astock 数据连接器）----------------
    def capital_flow(self, symbol: str) -> pd.DataFrame:
        """东财·个股资金流向（今日/5日/10日主力净流入）。"""
        return self._call("stock_individual_fund_flow", stock=symbol)

    def north_flow_hist(self) -> pd.DataFrame:
        """北向资金（沪深股通）每日净流入历史。"""
        return self._call("stock_hsgt_north_net_flow_in_em", symbol="北上")

    def dragon_tiger_detail(self, symbol: str, date: str) -> pd.DataFrame:
        """龙虎榜·个股明细（游资席位买卖）。date 格式 YYYYMMDD。"""
        return self._call("stock_lhb_detail_em", start_date=date, end_date=date)

    def valuation_indicator(self, symbol: str) -> pd.DataFrame:
        """个股估值指标（PE/PB/股息率等，乐咕口径）。"""
        return self._call("stock_a_indicator_lg", symbol=symbol)

    def unlock_detail(self, symbol: str = "") -> pd.DataFrame:
        """限售解禁明细（可按代码过滤）。"""
        if symbol:
            return self._call("stock_restricted_release_queue_em")
        return self._call("stock_restricted_release_queue_em")

    def margin_detail(self, symbol: str) -> pd.DataFrame:
        """融资融券·个股明细。"""
        return self._call("stock_margin_detail_szse", date="20500101")

    def main_fund_flow(self, symbol: str) -> pd.DataFrame:
        """东财·主力资金流向（个股近一月）。"""
        return self._call("stock_individual_fund_flow", stock=symbol, market="ab")

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """去除交易所后缀与空格，返回 6 位代码。"""
        code = str(symbol).strip().upper()
        for prefix in ("SH", "SZ", "BJ"):
            if code.startswith(prefix):
                code = code[len(prefix):]
        code = code.split(".")[0]
        return code.zfill(6) if code.isdigit() else code


# ============================================================
# v3.7新增：其他主流数据源适配器（插件式，用户有Key就能用）
# ============================================================

class TushareSource(DataSourceBase):
    """Tushare 数据源（需要 Token，积分制）。

    申请地址：https://tushare.pro/register?reg=7
    配置：在 .env 里加 TUSHARE_TOKEN=你的token
    """

    name = "tushare"

    def __init__(self, token: str = ""):
        self.token = token
        self._ts = None

    @property
    def ts(self):
        if self._ts is None:
            try:
                import tushare as ts  # noqa: PLC0415
            except ImportError as exc:
                raise DataSourceError("未安装 tushare，请先执行：pip install tushare") from exc
            if not self.token:
                raise DataSourceError("未配置 TUSHARE_TOKEN，请在 .env 里设置")
            ts.set_token(self.token)
            self._ts = ts.pro_api()
        return self._ts

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        return self.ts.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)

    def financial_abstract(self, symbol: str):
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        return self.ts.income(ts_code=ts_code)

    def stock_info(self, symbol: str):
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        return self.ts.stock_basic(ts_code=ts_code)


class MCPSource(DataSourceBase):
    """通过 MCP 协议接外部数据源（v3.7新增）。

    用法：
    1. 你有一个外部的 MCP Server（比如别的金融数据MCP）
    2. 在配置里指定 MCP Server 的地址
    3. 我们通过 MCP 协议调用它的工具取数

    注意：目前是占位实现，未来完善
    """

    name = "mcp"

    def __init__(self, server_url: str = "", tool_prefix: str = "stock_"):
        self.server_url = server_url
        self.tool_prefix = tool_prefix

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        raise DataSourceError("MCPSource 目前是占位实现，请用 AKShare 或 Tushare")

    def financial_abstract(self, symbol: str):
        raise DataSourceError("MCPSource 目前是占位实现，请用 AKShare 或 Tushare")

    def stock_info(self, symbol: str):
        raise DataSourceError("MCPSource 目前是占位实现，请用 AKShare 或 Tushare")


class OfflineSource(DataSourceBase):
    """离线样例数据源（容灾用，主数据源挂了自动切这个）。"""

    name = "offline"

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        raise DataSourceError("OfflineSource 只提供静态样例，不支持动态取数")

    def financial_abstract(self, symbol: str):
        raise DataSourceError("OfflineSource 只提供静态样例，不支持动态取数")

    def stock_info(self, symbol: str):
        raise DataSourceError("OfflineSource 只提供静态样例，不支持动态取数")


def build_data_source(source_name: str, **kwargs) -> DataSourceBase:
    """根据名称构建数据源（插件式工厂函数）。"""
    sources = {
        "akshare": AKShareSource,
        "tushare": TushareSource,
        "mcp": MCPSource,
        "offline": OfflineSource,
    }
    cls = sources.get(source_name)
    if cls is None:
        raise DataSourceError(f"未知数据源：{source_name}，可选：{list(sources.keys())}")
    return cls(**kwargs)
