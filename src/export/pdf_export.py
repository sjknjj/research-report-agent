"""报告导出 PDF（reportlab）。

支持中文，自动查找系统可用中文字体（macOS/Windows/Linux）。
把 Markdown 文本按标题、段落、表格、列表简单排版为多页 PDF。
未安装 reportlab 时返回 None 并给出安装提示，不阻塞主流程。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("export.pdf")

_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",                       # macOS
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/msyh.ttc",                                # Windows 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",   # Linux
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def _find_cjk_font() -> Optional[str]:
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def export_pdf(markdown_text: str, out_path: str, title: str = "研报") -> Optional[Path]:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                        Table, TableStyle)
    except ImportError:
        logger.info("未安装 reportlab，无法导出 PDF（pip install reportlab）")
        return None

    font_path = _find_cjk_font()
    if not font_path:
        logger.warning("未找到系统中文字体，PDF 中文可能显示为方块")
        font_name = "Helvetica"
        bold_name = "Helvetica-Bold"
    else:
        try:
            pdfmetrics.registerFont(TTFont("CJK", font_path))
            font_name = "CJK"
            bold_name = "CJK"
        except Exception:
            font_name = "Helvetica"
            bold_name = "Helvetica-Bold"

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


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("**", "").replace("*", ""))


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
