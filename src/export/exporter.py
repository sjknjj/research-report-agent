"""把 :class:`ResearchReport` 导出为 Markdown 与自包含 HTML 文件。"""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from ..common.config import PROJECT_ROOT
from ..common.logger import get_logger
from ..common.schema import ResearchReport
from .charts import financial_trend_chart, kline_with_volume, valuation_gauge

logger = get_logger("export")

_DISCLAIMER = (
    "本报告由 AI 自动生成，所引用资料来自公开网络，仅供研究参考，"
    "不构成任何投资建议；关键数据请以权威原始来源为准。"
)


def _slugify(topic: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", topic.strip())
    return text[:40] or "research_report"


def _resolve_output_dir(output_dir: str) -> Path:
    path = Path(output_dir)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_markdown(report: ResearchReport) -> str:
    """把研报渲染为 Markdown 字符串。"""
    lines: List[str] = [f"# {report.topic}", ""]

    if report.mode == "offline":
        lines += [
            "> ⚠️ **离线演示模式**：本报告未连接真实大模型与检索服务，",
            "> 内容为方法论模板与占位说明，不含真实数据，不构成投资建议。",
            "",
        ]

    generated = report.meta.get("generated_at", "")
    meta_bits = [f"生成时间：{generated}" if generated else ""]
    if report.meta.get("model"):
        meta_bits.append(f"模型：{report.meta['model']}")
    meta_bits.append(f"参考资料：{report.meta.get('source_count', len(report.references))} 条")
    lines.append("> " + "　|　".join(b for b in meta_bits if b))
    lines.append("")

    if report.meta.get("objective"):
        lines += [f"**研究目标**：{report.meta['objective']}", ""]

    lines += ["## 摘要", "", report.abstract.strip(), ""]

    for section in report.sections:
        lines += [f"## {section.heading}", "", section.content.strip(), ""]

    if report.references:
        lines += ["## 参考文献", ""]
        for ref in report.references:
            source_tag = f"（{ref.source}）" if ref.source else ""
            lines.append(f"{ref.index}. [{ref.title}]({ref.url}){source_tag}")
        lines.append("")

    lines += ["---", "", f"*{_DISCLAIMER}*", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 极简 Markdown -> HTML（仅覆盖本项目生成的语法，避免引入第三方依赖）
# ---------------------------------------------------------------------------
def _inline_md(text: str) -> str:
    escaped = html_lib.escape(text, quote=False)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        escaped,
    )
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def markdown_to_html(markdown: str) -> str:
    """把受控子集的 Markdown 转换为 HTML 片段。"""
    html_parts: List[str] = []
    list_items: List[str] = []
    quote_lines: List[str] = []

    def flush_list() -> None:
        if list_items:
            html_parts.append("<ul>" + "".join(f"<li>{_inline_md(i)}</li>" for i in list_items) + "</ul>")
            list_items.clear()

    def flush_quote() -> None:
        if quote_lines:
            html_parts.append(
                "<blockquote>" + "<br>".join(_inline_md(q) for q in quote_lines) + "</blockquote>"
            )
            quote_lines.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("### "):
            flush_list(); flush_quote()
            html_parts.append(f"<h3>{_inline_md(line[4:])}</h3>")
        elif line.startswith("## "):
            flush_list(); flush_quote()
            html_parts.append(f"<h2>{_inline_md(line[3:])}</h2>")
        elif line.startswith("# "):
            flush_list(); flush_quote()
            html_parts.append(f"<h1>{_inline_md(line[2:])}</h1>")
        elif line.startswith("> "):
            flush_list()
            quote_lines.append(line[2:])
        elif line.startswith("- "):
            flush_quote()
            list_items.append(line[2:])
        elif not line.strip():
            flush_list(); flush_quote()
        elif line.strip() in ("---",):
            flush_list(); flush_quote()
            html_parts.append("<hr>")
        else:
            flush_list(); flush_quote()
            html_parts.append(f"<p>{_inline_md(line)}</p>")

    flush_list(); flush_quote()
    return "\n".join(html_parts)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --ink:#1f2329; --muted:#6b7280; --line:#e5e7eb; --accent:#0f4c81; --bg:#ffffff; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#f5f6f8; color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
  line-height:1.8; }}
.container {{ max-width:860px; margin:0 auto; padding:48px 32px 80px; background:var(--bg);
  box-shadow:0 1px 12px rgba(0,0,0,.06); }}
h1 {{ font-size:28px; margin:0 0 16px; line-height:1.4; }}
h2 {{ font-size:21px; margin:36px 0 12px; padding-left:10px; border-left:4px solid var(--accent); }}
h3 {{ font-size:17px; margin:24px 0 8px; }}
p {{ margin:10px 0; text-align:justify; }}
blockquote {{ margin:14px 0; padding:12px 16px; background:#f0f5fa;
  border-left:4px solid var(--accent); color:#374151; font-size:14px; border-radius:4px; }}
ul {{ padding-left:22px; }} li {{ margin:6px 0; }}
a {{ color:var(--accent); text-decoration:none; word-break:break-all; }}
a:hover {{ text-decoration:underline; }}
hr {{ border:none; border-top:1px solid var(--line); margin:28px 0; }}
em {{ color:var(--muted); font-size:13px; }}
.charts {{ margin:16px 0; padding:12px; background:#fafbfc; border-radius:8px; border:1px solid var(--line); }}
.charts > div {{ margin:8px 0; }}
</style>
</head>
<body><div class="container">{body}</div></body></html>"""


def build_html(report: ResearchReport) -> str:
    body = markdown_to_html(build_markdown(report))
    return _HTML_TEMPLATE.format(title=html_lib.escape(report.topic), body=body)


def export_report(
    report: ResearchReport,
    output_dir: str = "output",
    formats: Tuple[str, ...] = ("md", "html"),
) -> List[Path]:
    """按指定格式把报告写入磁盘，返回生成的文件路径列表。"""
    report.meta.setdefault("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    target_dir = _resolve_output_dir(output_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{_slugify(report.topic)}_{stamp}"

    written: List[Path] = []
    if "md" in formats:
        md_path = target_dir / f"{base_name}.md"
        md_path.write_text(build_markdown(report), encoding="utf-8")
        written.append(md_path)
    if "html" in formats:
        html_path = target_dir / f"{base_name}.html"
        html_path.write_text(build_html(report), encoding="utf-8")
        written.append(html_path)

    for path in written:
        logger.info("已导出：%s", path)
    return written


# ---------------------------------------------------------------------------
# 投研助手结果导出（公司速览 / 公告要点 / 行业汇总）
# ---------------------------------------------------------------------------
_STATUS_MARK = {"supported": "✓ 一致", "mismatch": "✗ 矛盾", "unverifiable": "? 待核"}


def build_assistant_markdown(
    title: str,
    body: str,
    verification: Optional[dict] = None,
    fact_pack: str = "",
    meta_lines: Optional[List[str]] = None,
) -> str:
    """把投研助手结果渲染为 Markdown（含交叉校验报告与事实包附录）。"""
    lines = [f"# {title}", ""]
    if meta_lines:
        lines += ["> " + "　|　".join(m for m in meta_lines if m), ""]
    lines += [body.strip(), ""]

    if verification:
        lines += ["## 交叉校验报告（反幻觉）", ""]
        lines.append(
            f"**结论：{'通过' if verification.get('passed') else '未通过'}**　"
            f"共核对 {verification.get('total_claims', 0)} 处数值——"
            f"一致 {verification.get('supported', 0)}、"
            f"矛盾 {verification.get('mismatch', 0)}、"
            f"待核 {verification.get('unverifiable', 0)}、"
            f"疑似幻觉率 {verification.get('hallucination_rate', 0):.1%}"
        )
        lines.append("")
        if verification.get("claims"):
            lines += ["| 指标 | 状态 | 核对说明 |", "| --- | --- | --- |"]
            for claim in verification["claims"]:
                mark = _STATUS_MARK.get(claim.get("status"), claim.get("status", ""))
                note = claim.get("note", "").replace("|", "／")
                lines.append(f"| {claim.get('label', '')} | {mark} | {note} |")
            lines.append("")
        if verification.get("corrections"):
            lines.append("**自动纠错记录：**")
            lines += [f"- {c}" for c in verification["corrections"]]
            lines.append("")

    if fact_pack:
        lines += ["## 附录：已核实数据包（模型唯一可用的数字来源）", ""]
        lines += [f"> {l}" if l.strip() else ">" for l in fact_pack.splitlines()]
        lines.append("")

    lines += ["---", "", "*本内容由 AI 基于公开数据自动生成，仅供研究参考，不构成任何投资建议。*", ""]
    return "\n".join(lines)


def export_assistant(
    title: str,
    body: str,
    output_dir: str = "output",
    formats=("md", "html"),
    verification: Optional[dict] = None,
    fact_pack: str = "",
    meta_lines: Optional[List[str]] = None,
    chart_html: str = "",
    extra_html: str = "",
) -> List[Path]:
    """导出投研助手结果为 Markdown / HTML / PDF。"""
    target_dir = _resolve_output_dir(output_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{_slugify(title)}_{stamp}"
    markdown = build_assistant_markdown(title, body, verification, fact_pack, meta_lines)

    written: List[Path] = []
    if "md" in formats:
        path = target_dir / f"{base}.md"
        path.write_text(markdown, encoding="utf-8")
        written.append(path)
    if "html" in formats:
        body_html = markdown_to_html(markdown)
        if chart_html:
            body_html = f'<div class="charts">{chart_html}</div>' + body_html
        if extra_html:
            body_html = extra_html + body_html
        path = target_dir / f"{base}.html"
        path.write_text(
            _HTML_TEMPLATE.format(title=html_lib.escape(title), body=body_html),
            encoding="utf-8",
        )
        written.append(path)
    if "pdf" in formats:
        try:
            from .pdf_export import export_pdf
            pdf_path = export_pdf(markdown, str(target_dir / f"{base}.pdf"), title=title)
            if pdf_path:
                written.append(pdf_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF 导出失败：%s", exc)
    for path in written:
        logger.info("已导出：%s", path)
    return written
