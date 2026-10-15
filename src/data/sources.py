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
from datetime import datetime
from typing import Optional

import pandas as pd

from ..common.logger import get_logger
from . import sample_data

logger = get_logger("data.sources")


class DataSourceError(RuntimeError):
    """数据抓取异常。"""


def _exchange(symbol: str) -> str:
    """根据 6 位代码推断交易所：SH / SZ / BJ。

    北交所（4xx/8xx/920）之前被误判为深市，这里显式区分。
    """
    code = str(symbol).strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            code = code[len(prefix):]
    code = code.split(".")[0]
    if code.startswith("92"):
        return "BJ"  # 北交所新代码段（920xxx）
    if code.startswith(("6", "9", "5")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def _exchange_suffix(symbol: str, style: str = "dot") -> str:
    """返回带交易所后缀的代码。

    style：``dot``（600519.SH）/ ``jq``（600519.XSHG）/ ``prefix``（sh600519）。
    """
    ex = _exchange(symbol)
    code = str(symbol).strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            code = code[len(prefix):]
    code = code.split(".")[0]
    if style == "jq":
        return code + {"SH": ".XSHG", "SZ": ".XSHE", "BJ": ".XBSE"}[ex]
    if style == "prefix":
        return ex.lower() + code
    return code + "." + ex


def _retryable_call(func, retry: int = 3, backoff: float = 1.5, label: str = "", **kwargs):
    """对免费 HTTP 数据源做有限次重试。

    空 DataFrame 同样视为失败并重试——限流时接口常常返回空表而不是抛异常。
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, retry) + 1):
        try:
            data = func(**kwargs)
            if data is None or (hasattr(data, "empty") and data.empty):
                raise DataSourceError(f"{label or '接口'} 返回空数据")
            return data
        except Exception as exc:  # noqa: BLE001 - 免费接口异常类型繁杂，统一重试
            last_exc = exc
            if attempt < max(1, retry):
                wait = backoff * attempt
                logger.info(
                    "%s 第 %d 次调用失败（%s），%.1fs 后重试",
                    label or "接口", attempt, type(exc).__name__, wait,
                )
                time.sleep(wait)
    raise DataSourceError(f"{label or '接口'} 重试 {retry} 次仍失败：{last_exc}")


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
        """带重试地调用 akshare 接口函数（空结果也会重试，限流常表现为空表）。"""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retry + 1):
            try:
                func = getattr(self.ak, func_name)
                df = func(**kwargs)
                if df is None or (hasattr(df, "empty") and df.empty):
                    raise DataSourceError(f"接口 {func_name} 返回空数据")
                return df
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
        """限售解禁明细（按个股代码查询）。"""
        if not symbol:
            raise DataSourceError("unlock_detail 需要提供股票代码 symbol")
        return self._call("stock_restricted_release_queue_em", symbol=symbol)

    def margin_detail(self, symbol: str) -> pd.DataFrame:
        """融资融券·个股明细（按交易所选择接口，日期取最近交易日）。"""
        if not symbol:
            raise DataSourceError("margin_detail 需要提供股票代码 symbol")
        # 不要硬编码未来日期；用当天日期（非交易日接口会返回空 → 重试后抛出，由上层容灾）
        date = datetime.now().strftime("%Y%m%d")
        ex = _exchange(symbol)
        if ex == "SH":
            df = self._call("stock_margin_detail_sse", date=date)
        else:
            # 深市 / 北交所走深市接口
            df = self._call("stock_margin_detail_szse", date=date)
        # 只保留该股票的记录，避免把全市场明细当个股数据
        code_col = next(
            (c for c in ("证券代码", "标的证券代码", "代码") if c in df.columns), None
        )
        if code_col is not None:
            filtered = df[df[code_col].astype(str).str.contains(str(symbol), na=False)]
            if filtered.empty:
                raise DataSourceError(f"融资融券明细中未找到 {symbol}")
            return filtered
        return df

    def main_fund_flow(self, symbol: str) -> pd.DataFrame:
        """东财·主力资金流向（个股近一月，market 只能取 sh/sz/bj）。"""
        market = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[_exchange(symbol)]
        return self._call("stock_individual_fund_flow", stock=symbol, market=market)

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
        ts_code = _exchange_suffix(symbol, "dot")
        # tushare 的 pro_bar 支持复权参数，不能把 adjust 丢掉（否则拿到的是不复权价）
        params = {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}
        if adjust in ("qfq", "hfq"):
            params["adj"] = adjust
        return self.ts.pro_bar(**params)

    def financial_abstract(self, symbol: str):
        ts_code = _exchange_suffix(symbol, "dot")
        return self.ts.income(ts_code=ts_code)

    def stock_info(self, symbol: str):
        ts_code = _exchange_suffix(symbol, "dot")
        return self.ts.stock_basic(ts_code=ts_code)


class MCPSource(DataSourceBase):
    """通过 MCP 协议接外部数据源（v3.7新增）。

    用法：
    1. 你有一个外部的 MCP Server（比如别的金融数据MCP）
    2. 在配置里指定 MCP Server 的地址
    3. 我们通过 MCP 协议调用它的工具取数

    注意：目前是占位实现，所有方法都会抛出 DataSourceError；
    配置数据源时请把它放在其它可用数据源之后作为“不可用占位”。
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
    """离线样例数据源（容灾链的最后一环，返回内置虚构样例而不是抛异常）。

    注意：返回的是 ``data/samples`` 下的静态样例，与请求的 symbol 无关；
    ``DataService`` 会在容灾切到本数据源时把 ``source`` 如实标注为 ``sample``。
    """

    name = "offline"

    def _ensure_samples(self) -> None:
        missing = sample_data.ensure_samples_exist()
        if missing:
            raise DataSourceError(f"离线样例不存在，请先运行 python {missing}")

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        self._ensure_samples()
        return sample_data.sample_price_raw().copy()

    def financial_abstract(self, symbol: str):
        self._ensure_samples()
        return sample_data.sample_financial_raw().copy()

    def stock_info(self, symbol: str):
        # 样例数据没有个股信息，返回两列长表供 clean_info 使用（明确标注为离线样例）
        return pd.DataFrame(
            {
                "item": ["数据来源", "说明"],
                "value": ["离线虚构样例", "非真实证券，仅用于演示"],
            }
        )


# ============================================================
# v3.7新增：更多主流数据源（免费HTTP API + 付费商业数据源）
# ============================================================

class EastMoneyHttpSource(DataSourceBase):
    """东方财富数据源（免费，基于 AKShare 东财接口）。

    说明：不是真的裸调HTTP，而是用 AKShare 封装好的东财接口，
    这样和 SinaHttpSource / TencentHttpSource 形成三个独立数据源，
    可以做交叉验证（同一个数字从东财/新浪/腾讯分别取，比对是否一致）。
    """

    name = "eastmoney_http"

    def __init__(self, retry: int = 3, backoff: float = 1.5):
        self.retry = retry
        self.backoff = backoff
        self._ak = None

    @property
    def ak(self):
        if self._ak is None:
            try:
                import akshare as ak  # noqa: PLC0415
            except ImportError as exc:
                raise DataSourceError("未安装 akshare，请先执行：pip install akshare pandas") from exc
            self._ak = ak
        return self._ak

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        return _retryable_call(
            self.ak.stock_zh_a_hist, self.retry, self.backoff, "东财日线",
            symbol=symbol, period="daily",
            start_date=start_date, end_date=end_date, adjust=adjust,
        )

    def financial_abstract(self, symbol: str):
        # 东财版财务摘要：主接口失败再换备用接口，两个接口都带重试
        try:
            return _retryable_call(
                self.ak.stock_financial_analysis_indicator, self.retry, self.backoff,
                "东财财务指标", symbol=symbol,
            )
        except Exception:
            return _retryable_call(
                self.ak.stock_financial_abstract, self.retry, self.backoff,
                "东财财务摘要", symbol=symbol,
            )

    def stock_info(self, symbol: str):
        return _retryable_call(
            self.ak.stock_individual_info_em, self.retry, self.backoff,
            "东财个股信息", symbol=symbol,
        )


class SinaHttpSource(DataSourceBase):
    """新浪财经数据源（免费，基于 AKShare 新浪接口）。

    用来和东财/腾讯做交叉验证。
    """

    name = "sina_http"

    def __init__(self, retry: int = 3, backoff: float = 1.5):
        self.retry = retry
        self.backoff = backoff
        self._ak = None

    @property
    def ak(self):
        if self._ak is None:
            try:
                import akshare as ak  # noqa: PLC0415
            except ImportError as exc:
                raise DataSourceError("未安装 akshare，请先执行：pip install akshare pandas") from exc
            self._ak = ak
        return self._ak

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        # 新浪版日线（bj 前缀供北交所使用）
        return _retryable_call(
            self.ak.stock_zh_a_daily, self.retry, self.backoff, "新浪日线",
            symbol=_exchange_suffix(symbol, "prefix"), adjust=adjust,
        )

    def financial_abstract(self, symbol: str):
        # 新浪版财务
        try:
            return _retryable_call(
                self.ak.stock_financial_report_sina, self.retry, self.backoff,
                "新浪财务", stock=symbol, symbol="资产负债表",
            )
        except Exception as exc:
            raise DataSourceError(f"新浪财务接口取数失败：{exc}")

    def stock_info(self, symbol: str):
        """新浪实时行情，**过滤出该股票**并整理为 item/value 两列长表。

        修复前返回的是东财全市场快照（``stock_zh_a_spot_em``），
        会被 clean_info 当成该股票的信息拼成垃圾。
        """
        df = _retryable_call(
            self.ak.stock_zh_a_spot, self.retry, self.backoff, "新浪实时行情",
        )
        code_col = next(
            (c for c in ("代码", "symbol", "代码编号") if c in df.columns), None
        )
        if code_col is None:
            raise DataSourceError("新浪实时行情缺少代码列，无法定位个股")
        row = df[df[code_col].astype(str).str.contains(str(symbol), na=False)]
        if row.empty:
            raise DataSourceError(f"新浪实时行情中未找到 {symbol}")
        record = row.iloc[0]
        return pd.DataFrame(
            {
                "item": [str(c) for c in record.index],
                "value": [str(record[c]) for c in record.index],
            }
        )


class TencentHttpSource(DataSourceBase):
    """腾讯财经数据源（免费，基于 AKShare 腾讯接口）。

    用来和东财/新浪做交叉验证。
    """

    name = "tencent_http"

    def __init__(self, retry: int = 3, backoff: float = 1.5):
        self.retry = retry
        self.backoff = backoff
        self._ak = None

    @property
    def ak(self):
        if self._ak is None:
            try:
                import akshare as ak  # noqa: PLC0415
            except ImportError as exc:
                raise DataSourceError("未安装 akshare，请先执行：pip install akshare pandas") from exc
            self._ak = ak
        return self._ak

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        # 腾讯版日线（接口本身不支持复权，adjust 仅作签名兼容）
        return _retryable_call(
            self.ak.stock_zh_a_hist_tx, self.retry, self.backoff, "腾讯日线",
            symbol=_exchange_suffix(symbol, "prefix"),
        )

    def financial_abstract(self, symbol: str):
        # 腾讯财务接口暂未接入
        raise DataSourceError("腾讯财务接口暂未接入，用东财或新浪")

    def stock_info(self, symbol: str):
        return _retryable_call(
            self.ak.stock_individual_basic_info_xq, self.retry, self.backoff,
            "腾讯个股信息", symbol=symbol,
        )


# ============================================================
# 付费商业数据源（占位，用户有授权就能用）
# ============================================================

class WindSource(DataSourceBase):
    """Wind 万得（付费，商业授权）。

    使用方法：
    1. 安装 Wind 终端并登录
    2. pip install WindPy
    3. 直接就能用，不用配 token
    """

    name = "wind"

    def __init__(self):
        self._w = None

    @property
    def w(self):
        if self._w is None:
            try:
                from WindPy import w  # noqa: PLC0415
            except ImportError as exc:
                raise DataSourceError(
                    "未安装 WindPy，请先安装 Wind 终端，然后 pip install WindPy"
                ) from exc
            w.start()
            self._w = w
        return self._w

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        wind_symbol = _exchange_suffix(symbol, "dot")
        return self.w.wsd(wind_symbol, "open,high,low,close,volume", start_date, end_date, "")

    def financial_abstract(self, symbol: str):
        wind_symbol = _exchange_suffix(symbol, "dot")
        return self.w.wss(wind_symbol, "net_profit,total_revenue,roe,gross_margin")

    def stock_info(self, symbol: str):
        wind_symbol = _exchange_suffix(symbol, "dot")
        return self.w.wss(wind_symbol, "pe_ttm,pb_lf,total_mkt_cap")


class ChoiceSource(DataSourceBase):
    """东方财富 Choice（付费，商业授权）。

    使用方法：
    1. 安装 Choice 终端并登录
    2. pip install choice-sdk
    3. 直接就能用
    """

    name = "choice"

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from choice_sdk import ChoiceClient  # noqa: PLC0415
            except ImportError as exc:
                raise DataSourceError(
                    "未安装 choice_sdk，请先安装 Choice 终端，然后 pip install choice-sdk"
                ) from exc
            self._client = ChoiceClient()
        return self._client

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        return self.client.get_kline(symbol, start_date, end_date)

    def financial_abstract(self, symbol: str):
        return self.client.get_financial(symbol)

    def stock_info(self, symbol: str):
        return self.client.get_stock_info(symbol)


class iFinDSource(DataSourceBase):
    """同花顺 iFinD（付费，商业授权）。

    使用方法：
    1. 安装 iFinD 终端并登录
    2. pip install ifind-sdk
    3. 直接就能用
    """

    name = "ifind"

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from ifind_sdk import iFinDClient  # noqa: PLC0415
            except ImportError as exc:
                raise DataSourceError(
                    "未安装 ifind_sdk，请先安装 iFinD 终端，然后 pip install ifind-sdk"
                ) from exc
            self._client = iFinDClient()
        return self._client

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        return self.client.get_kline(symbol, start_date, end_date)

    def financial_abstract(self, symbol: str):
        return self.client.get_financial(symbol)

    def stock_info(self, symbol: str):
        return self.client.get_stock_info(symbol)


class JQDataSource(DataSourceBase):
    """聚宽 JQData（付费，量化数据源）。

    使用方法：
    1. 注册 JQData 账号（https://www.joinquant.com）
    2. pip install jqdatasdk
    3. 在 .env 里配 JQDATA_USERNAME 和 JQDATA_PASSWORD
    """

    name = "jqdata"

    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password
        self._auth = False

    def _login(self):
        if self._auth:
            return
        try:
            import jqdatasdk as jq  # noqa: PLC0415
        except ImportError as exc:
            raise DataSourceError("未安装 jqdatasdk，请先执行：pip install jqdatasdk") from exc
        if not self.username or not self.password:
            raise DataSourceError("未配置 JQDATA_USERNAME 和 JQDATA_PASSWORD")
        jq.auth(self.username, self.password)
        self._auth = True

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        self._login()
        import jqdatasdk as jq
        jq_symbol = _exchange_suffix(symbol, "jq")
        # jqdatasdk 的复权参数只认 pre/post，直接把 qfq 传进去会报错
        fq = {"qfq": "pre", "hfq": "post"}.get(adjust)
        return jq.get_price(jq_symbol, start_date=start_date, end_date=end_date, fq=fq)

    def financial_abstract(self, symbol: str):
        self._login()
        import jqdatasdk as jq
        jq_symbol = _exchange_suffix(symbol, "jq")
        return jq.get_fundamentals(jq.query(jq.income).filter(jq.income.code == jq_symbol))

    def stock_info(self, symbol: str):
        self._login()
        import jqdatasdk as jq
        jq_symbol = _exchange_suffix(symbol, "jq")
        return jq.get_security_info(jq_symbol)


class RiceQuantSource(DataSourceBase):
    """米筐 RiceQuant（付费，量化数据源）。

    使用方法：
    1. 注册 RiceQuant 账号（https://www.ricequant.com）
    2. pip install rqdatac
    3. 在 .env 里配 RQDATA_USERNAME 和 RQDATA_PASSWORD
    """

    name = "ricequant"

    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password
        self._init = False

    def _init_client(self):
        if self._init:
            return
        try:
            import rqdatac as rq  # noqa: PLC0415
        except ImportError as exc:
            raise DataSourceError("未安装 rqdatac，请先执行：pip install rqdatac") from exc
        if not self.username or not self.password:
            raise DataSourceError("未配置 RQDATA_USERNAME 和 RQDATA_PASSWORD")
        rq.init(self.username, self.password)
        self._init = True

    def price_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"):
        self._init_client()
        import rqdatac as rq
        rq_symbol = _exchange_suffix(symbol, "jq")
        # rqdatac 的 adjust_type 只认 pre/post/none，不能直接传 qfq
        adjust_type = {"qfq": "pre", "hfq": "post"}.get(adjust)
        return rq.get_price(rq_symbol, start_date=start_date, end_date=end_date, adjust_type=adjust_type)

    def financial_abstract(self, symbol: str):
        self._init_client()
        import rqdatac as rq
        rq_symbol = _exchange_suffix(symbol, "jq")
        return rq.get_fundamentals(rq_symbol, start_date="20200101")

    def stock_info(self, symbol: str):
        self._init_client()
        import rqdatac as rq
        rq_symbol = _exchange_suffix(symbol, "jq")
        return rq.get_security(rq_symbol)


def build_data_source(source_name: str, **kwargs) -> DataSourceBase:
    """根据名称构建数据源（插件式工厂函数）。"""
    sources = {
        # 免费
        "akshare": AKShareSource,
        "eastmoney_http": EastMoneyHttpSource,
        "sina_http": SinaHttpSource,
        "tencent_http": TencentHttpSource,
        # 付费
        "tushare": TushareSource,
        "wind": WindSource,
        "choice": ChoiceSource,
        "ifind": iFinDSource,
        "jqdata": JQDataSource,
        "ricequant": RiceQuantSource,
        # 其他
        "mcp": MCPSource,
        "offline": OfflineSource,
    }
    cls = sources.get(source_name)
    if cls is None:
        raise DataSourceError(f"未知数据源：{source_name}，可选：{list(sources.keys())}")
    return cls(**kwargs)
