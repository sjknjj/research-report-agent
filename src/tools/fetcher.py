"""网页正文抓取工具（标准库实现，失败安全）。"""
from __future__ import annotations

import gzip
import io
import re
import urllib.error
import urllib.request

from ..common.logger import get_logger

logger = get_logger("tools.fetcher")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 单页最多读取的原始字节数，避免超大页面把内存吃满
_MAX_READ_BYTES = 2_000_000

# 编码嗅探时最多看多少字节的 head
_HEAD_BYTES = 4096


def _charset_from_header(content_type: str) -> str:
    """从 Content-Type 响应头里取真实 charset（优先于页面 meta 猜测）。"""
    match = re.search(r'charset\s*=\s*["\']?([\w-]+)', content_type or "", re.IGNORECASE)
    return match.group(1) if match else ""


def _charset_from_meta(raw: bytes) -> str:
    """从 HTML 头部字节里嗅探 meta charset。"""
    head = raw[:_HEAD_BYTES].decode("ascii", errors="ignore")
    match = re.search(r'charset\s*=\s*["\']?([\w-]+)', head, re.IGNORECASE)
    return match.group(1) if match else ""


def _decode_body(raw: bytes, content_type: str) -> str:
    """解压（如需）并按正确编码解码字节流。

    编码优先取 ``Content-Type`` 的 charset，其次页面 meta，最后按
    utf-8 → gb18030 兜底（GBK 财经页不再乱码）。
    """
    if not raw:
        return ""
    # 处理 gzip（损坏的 gzip 交给调用方统一捕获）
    if raw[:2] == b"\x1f\x8b" or "gzip" in (content_type or "").lower():
        try:
            raw = gzip.decompress(raw)
        except OSError:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()

    candidates = [_charset_from_header(content_type), _charset_from_meta(raw), "utf-8", "gb18030"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return raw.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="ignore")


def extract_main_text(html: str) -> str:
    """从 HTML 中粗略提取可读正文（移除脚本/样式/标签并压缩空白）。"""
    html = re.sub(r"(?is)<(script|style|noscript|header|footer|nav).*?</\1>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    # 块级标签转换行，保留段落结构
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article|br)>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    paragraphs = [line for line in lines if len(line) >= 20]
    return "\n".join(paragraphs)


def fetch_page(url: str, max_chars: int = 2000, timeout: int = 15) -> str:
    """抓取单个网页并返回截断后的纯文本正文；任何异常都返回空字符串。"""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            # 限制读取字节数，避免超大响应拖垮进程
            raw = resp.read(_MAX_READ_BYTES)

        if "text/html" not in content_type and not url.endswith((".html", "/")):
            # 非网页资源（pdf/图片等）不做正文提取
            return ""

        # 解码也在 try 内：损坏 gzip（zlib.error）等同样返回空串，兑现失败安全承诺
        text = extract_main_text(_decode_body(raw, content_type))
    except Exception as exc:  # noqa: BLE001 - 网络/解压/解码任何异常都返回空串
        logger.info("抓取失败（已跳过）%s：%s", url, exc)
        return ""

    if len(text) > max_chars:
        text = text[:max_chars] + "……"
    return text
