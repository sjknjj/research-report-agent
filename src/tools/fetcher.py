"""网页正文抓取工具（标准库实现，失败安全）。"""
from __future__ import annotations

import gzip
import io
import re
import urllib.error
import urllib.request
from typing import Optional

from ..common.logger import get_logger

logger = get_logger("tools.fetcher")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _decode_body(raw: bytes, content_type: str) -> str:
    # 处理 gzip
    if content_type.lower().find("gzip") >= 0 or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()

    # 从 meta charset 推断编码，默认 utf-8
    encoding = "utf-8"
    head = raw[:2048].decode("ascii", errors="ignore").lower()
    match = re.search(r'charset=["\']?([\w-]+)', head)
    if match:
        encoding = match.group(1)
    try:
        return raw.decode(encoding, errors="ignore")
    except (LookupError, UnicodeDecodeError):
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
            raw = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        logger.info("抓取失败（已跳过）%s：%s", url, exc)
        return ""

    if "text/html" not in content_type and not url.endswith((".html", "/")):
        # 非网页资源（pdf/图片等）不做正文提取
        return ""

    text = extract_main_text(_decode_body(raw, content_type))
    if len(text) > max_chars:
        text = text[:max_chars] + "……"
    return text
