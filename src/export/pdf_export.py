"""报告导出 PDF（reportlab）。

支持中文，自动查找系统可用中文字体（macOS/Windows/Linux）。
把 Markdown 文本按标题、段落、表格、列表简单排版为多页 PDF。

失败语义：
- 未安装 reportlab：返回 None（调用方可通过返回值判定失败）；
- 找不到可注册的中文字体：抛出 :class:`PdfExportError`，
  绝不静默降级成 Helvetica 产出中文全丢的 PDF。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("export.pdf")

class PdfExportError(RuntimeError):
    """PDF 导出失败（缺中文字体 / 缺依赖 / 排版异常）。"""


_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",                       # macOS
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/msyh.ttc",                                # Windows 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",   # Linux (fonts-noto-cjk)
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

# 兜底搜索：不同发行版字体路径差异较大，按文件名特征扫描常见目录
_FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts")]
_FONT_NAME_HINTS = (
    "notosanscjk", "notoserifcjk", "sourcehansans", "sourcehanserif",
    "wqy", "droidsansfallback", "msyh", "simhei", "simsun",
)


def _find_cjk_font() -> Optional[str]:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    for directory in _FONT_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() not in (".ttf", ".ttc", ".otf", ".otc"):
                continue
            name = path.name.lower()
            if any(hint in name for hint in _FONT_NAME_HINTS):
                return str(path)
    return None


def export_pdf(markdown_text: str, out_path: str, title: str = "研报") -> Optional[Path]:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        logger.info("未安装 reportlab，无法导出 PDF（pip install reportlab）")
        return None

    font_path = _find_cjk_font()
    if not font_path:
        # 明确失败：宁可不出 PDF，也不要产出中文全是方块的报告
        raise PdfExportError(
            "未找到可用的中文字体（CJK），已中止 PDF 导出以避免中文丢失。"
            "请安装中文字体，例如 Debian/Ubuntu：apt-get install -y fonts-noto-cjk；"
            "或 macOS/Windows 自带的苹方/黑体/微软雅黑。"
        )
    try:
        pdfmetrics.registerFont(TTFont("CJK", font_path))
    except Exception as exc:  # noqa: BLE001
        raise PdfExportError(f"注册中文字体失败（{font_path}）：{exc}") from exc
    font_name = "CJK"
    bold_name = "CJK"

    styles = {
        "h1": ParagraphStyle("h1", fontName=bold_name, fontSize=19, leading=26,
                             spaceAfter=10, textColor=colors.HexColor("#1a2733")),
        "h2": ParagraphStyle("h2", fontName=bold_name, fontSize=14, leading=20,
                             spaceBefore=12, spaceAfter=6,
                             textColor=colors.HexColor("#0984e3")),
        "h3": ParagraphStyle("h3", fontName=bold_name, fontSize=12, leading=18,
                             spaceBefore=8, spaceAfter=4,
                             textColor=colors.HexColor("#2d3436")),
        "body": ParagraphStyle("body", fontName=font_name, fontSize=10.2,
                               leading=17, spaceAfter=4),
        "quote": ParagraphStyle("quote", fontName=font_name, fontSize=9.5,
                                leading=15, leftIndent=10, textColor=colors.grey),
    }

    story = []
    lines = markdown_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        # 表格块：连续的 | 开头行
        if stripped.startswith("|"):
            tbl_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl_lines.append(lines[i].strip())
                i += 1
            table = _build_table(tbl_lines, styles["body"], font_name)
            if table:
                story.append(table)
                story.append(Spacer(1, 6))
            continue

        if not stripped:
            story.append(Spacer(1, 3))
        elif re.match(r"^#{1,6}\s+", stripped):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("#").strip()
            key = "h1" if level == 1 else ("h2" if level == 2 else "h3")
            story.append(Paragraph(_esc(text), styles[key]))
        elif stripped.startswith("> "):
            story.append(Paragraph(_esc(stripped[2:]), styles["quote"]))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            story.append(Paragraph("• " + _esc(stripped[2:]), styles["body"]))
        elif re.match(r"^\d+\.\s", stripped):
            story.append(Paragraph(_esc(stripped), styles["body"]))
        elif stripped in ("---", "***"):
            story.append(Spacer(1, 4))
        else:
            story.append(Paragraph(_esc(stripped), styles["body"]))
        i += 1

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=title)
    doc.build(story)
    logger.info("PDF 已导出：%s", out)
    return out


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _esc(text: str) -> str:
    """转义 XML 特殊字符，并把 Markdown 内联标记转成 reportlab 支持的标签。

    - ``**x**`` -> ``<b>x</b>``
    - ``[标题](URL)`` -> ``标题（URL）``
    - ``*x*`` -> ``<i>x</i>``（不再无差别删除星号）
    """
    out = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = _MD_LINK_RE.sub(lambda m: f"{m.group(1)}（{m.group(2)}）", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", out)
    return out


def _build_table(md_lines: List[str], body_style, font_name):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    rows = []
    for ln in md_lines:
        if re.match(r"^\|[\s:|-]+\|?$", ln.replace(" ", "")):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if any(cells):
            rows.append([_esc(c) for c in cells])
    if not rows:
        return None
    # 表格字号小一些
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import ParagraphStyle
    cell_style = ParagraphStyle("cell", fontName=font_name, fontSize=8, leading=11)
    data = [[Paragraph(c, cell_style) for c in r] for r in rows]
    ncols = max(len(r) for r in data)
    width = 174 * mm / ncols
    table = Table(data, colWidths=[width] * ncols, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0984e3")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dfe6e9")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f7fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table
