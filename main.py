#!/usr/bin/env python3
"""研报辅助 Agent 命令行入口。

子命令：
    brief SYMBOL            公司投研速览（AKShare 抓 A 股数据 → 大模型 → 交叉校验，含技术面/产业链图）
    announce                公告要点提炼（--file 指定公告文本，缺省用样例公告）
    industry NAME           行业信息汇总
    compare S1 S2 ...       多公司横向对比与行业内排序（--by 指定排序维度）
    report TOPIC            通用多智能体研报（规划→检索→分析→撰写→审校）
    serve                   启动 FastAPI Web 界面（端口 8000）
    demo                    一键离线案例（含"反幻觉交叉校验"演示）

通用参数：
    --pdf                   额外导出 PDF（需 reportlab）
    --md-only               只导出 Markdown
    --sample                离线样例（无需密钥/网络）
    --deep                  report 专用，递归深挖检索

示例：
    python main.py demo
    python main.py brief 600519 --pdf              # 在线：真实抓取 + 大模型 + PDF
    python main.py brief 000000 --sample           # 离线样例，无需密钥/网络
    python main.py compare 600519 000858 --by pe   # 多公司按市盈率排序
    python main.py announce --file notice.txt --symbol 600519
    python main.py industry 白酒
    python main.py report 低空经济产业链 --deep
    python main.py serve
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 保证从任意工作目录运行都能导入 src 包
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.orchestrator import ResearchOrchestrator  # noqa: E402
from src.common.config import load_settings  # noqa: E402
from src.common.logger import setup_logging, get_logger  # noqa: E402
from src.data import sample_data  # noqa: E402
from src.data.data_service import DataService  # noqa: E402
from src.export.charts import financial_trend_chart, kline_with_volume  # noqa: E402
from src.export.exporter import (  # noqa: E402
    export_assistant,
    export_report,
)
from src.services.research_assistant import ResearchAssistant  # noqa: E402
from src.verify.facts import FactBase  # noqa: E402
from src.verify.verifier import CrossVerifier  # noqa: E402

_COMMANDS = {"brief", "announce", "industry", "report", "compare", "demo", "serve", "daily"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-report-agent",
        description="基于大模型 + AKShare 的 A 股研报辅助 Agent",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=None, help="自定义 config.json 路径")
    common.add_argument("--mode", choices=["auto", "online", "offline"], default=None)
    common.add_argument("--model", default=None, help="覆盖 LLM_MODEL")
    common.add_argument("--output", default=None, help="输出目录")
    common.add_argument("--rounds", type=int, default=None, help="校验后最大修订轮数")
    common.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    common.add_argument("--md-only", action="store_true", help="只导出 Markdown")
    common.add_argument("--pdf", action="store_true", help="额外导出 PDF（需 reportlab）")

    sub = parser.add_subparsers(dest="command")

    p_brief = sub.add_parser("brief", parents=[common], help="公司投研速览")
    p_brief.add_argument("symbol", help="A 股代码，如 600519")
    p_brief.add_argument("--start", default=None, help="行情起始 YYYYMMDD")
    p_brief.add_argument("--end", default=None, help="行情结束 YYYYMMDD")
    p_brief.add_argument("--sample", action="store_true", help="强制使用离线样例数据")
    p_brief.add_argument("--push", action="store_true", help="3.0：生成完自动推送到飞书/企业微信")

    p_ann = sub.add_parser("announce", parents=[common], help="公告要点提炼")
    p_ann.add_argument("--file", default=None, help="公告文本文件路径（缺省用样例公告）")
    p_ann.add_argument("--title", default="公司公告", help="公告标题")
    p_ann.add_argument("--symbol", default=None, help="可选：股票代码，用于公告↔财报交叉校验")
    p_ann.add_argument("--sample", action="store_true", help="强制使用离线样例")

    p_ind = sub.add_parser("industry", parents=[common], help="行业信息汇总")
    p_ind.add_argument("name", nargs="+", help="行业板块名称，如 白酒")

    p_rep = sub.add_parser("report", parents=[common], help="通用多智能体研报")
    p_rep.add_argument("topic", nargs="+", help="研究主题")
    p_rep.add_argument("--provider", choices=["auto", "duckduckgo", "tavily", "mock"], default=None)
    p_rep.add_argument("--deep", action="store_true", help="启用递归深挖检索（多轮补检）")

    p_cmp = sub.add_parser("compare", parents=[common], help="多公司横向对比与排序")
    p_cmp.add_argument("symbols", nargs="+", help="A 股代码，多个用空格分隔，如 600519 000858")
    p_cmp.add_argument("--by", default="profit_yoy",
                       help="排序维度：profit_yoy/revenue_yoy/net_profit/pe/pb/roe/period_return")
    p_cmp.add_argument("--sample", action="store_true", help="强制使用离线样例")

    sub.add_parser("demo", parents=[common], help="一键离线案例 + 反幻觉演示")
    sub.add_parser("serve", parents=[common], help="启动 FastAPI Web 界面（端口 8000）")

    p_daily = sub.add_parser("daily", parents=[common], help="自选股批量跑 + 推送（配合 GitHub Actions 定时）")
    p_daily.add_argument("--file", default="watchlist.txt", help="自选股列表文件路径")
    p_daily.add_argument("--push", action="store_true", default=True, help="生成完自动推送（默认开启）")
    return parser


def _settings(args):
    overrides = {
        "mode": getattr(args, "mode", None),
        "llm_model": getattr(args, "model", None),
        "output_dir": getattr(args, "output", None),
        "max_review_rounds": getattr(args, "rounds", None),
        "log_level": getattr(args, "log_level", None),
        "search_provider": getattr(args, "provider", None),
    }
    settings = load_settings(getattr(args, "config", None), cli_overrides=overrides)
    setup_logging(settings.log_level)
    return settings


def _formats(settings, args):
    if getattr(args, "md_only", False):
        return ("md",)
    fmts = tuple(settings.export_formats)
    if getattr(args, "pdf", False) and "pdf" not in fmts:
        fmts = fmts + ("pdf",)
    return fmts


# ---------------------------------------------------------------------------
def cmd_brief(args) -> int:
    logger = get_logger("cli.brief")
    settings = _settings(args)
    prefer_live = (not args.sample) and settings.effective_mode != "offline"
    ra = ResearchAssistant(settings)
    result = ra.company_brief(args.symbol, args.start, args.end, prefer_live=prefer_live)

    bundle = result.bundle or {}
    title = f"{bundle.get('name', args.symbol)} 投研速览"
    meta = [f"数据来源：{bundle.get('source', '-')}", f"截至：{bundle.get('as_of', '-')}",
            f"模式：{settings.effective_mode}"]

    # 生成图表（未安装 plotly 时静默跳过）
    chart_html = ""
    raw = result.raw_bundle
    if raw is not None and raw.price_clean is not None:
        chart_html = kline_with_volume(raw.price_clean)
    if raw is not None and raw.financial_clean is not None:
        fin_chart = financial_trend_chart(raw.financial_clean)
        if fin_chart:
            chart_html = (chart_html or "") + fin_chart

    files = export_assistant(
        title, result.text, settings.output_dir, _formats(settings, args),
        verification=result.verification, fact_pack=result.fact_pack, meta_lines=meta,
        chart_html=chart_html, extra_html=result.extra.get("chain_graph", ""),
    )
    v = result.verification or {}
    print("\n" + "=" * 64)
    print(title)
    print(f"交叉校验：{'通过' if v.get('passed') else '未通过'}｜"
          f"一致 {v.get('supported', 0)}、矛盾 {v.get('mismatch', 0)}、"
          f"待核 {v.get('unverifiable', 0)}、幻觉率 {v.get('hallucination_rate', 0):.1%}")
    for path in files:
        print("已生成：", path)
    print("=" * 64)
    logger.info("公司速览完成")

    # 3.0：可选推送飞书/企业微信
    if getattr(args, "push", False):
        _push_brief(title, result.text, v, settings)
    return 0


def _push_brief(title: str, text: str, verification: dict, settings) -> None:
    """3.0：把速览结果推送到飞书/企业微信（按配置自动选择渠道）。"""
    import os
    from src.notify import FeishuNotifier, WeComNotifier

    feishu_url = os.getenv("FEISHU_WEBHOOK_URL", "")
    wecom_key = os.getenv("WECOM_WEBHOOK_KEY", "")

    if not feishu_url and not wecom_key:
        print("推送：未配置 FEISHU_WEBHOOK_URL / WECOM_WEBHOOK_KEY，跳过")
        return

    v = verification or {}
    summary = (
        f"**{title}**\n\n"
        f"- 交叉校验：{'通过' if v.get('passed') else '未通过'}"
        f"（一致{v.get('supported', 0)}、矛盾{v.get('mismatch', 0)}）\n"
        f"- 幻觉率：{v.get('hallucination_rate', 0):.1%}\n\n"
        f"{text[:800]}..."
    )

    if feishu_url:
        feishu = FeishuNotifier(feishu_url, os.getenv("FEISHU_SECRET"))
        if feishu.send_markdown(title, summary):
            print("已推送：飞书")
    if wecom_key:
        wecom = WeComNotifier(wecom_key)
        if wecom.send_markdown(summary):
            print("已推送：企业微信")


def cmd_announce(args) -> int:
    settings = _settings(args)
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sample_data.sample_announcement_text()
    prefer_live = (not args.sample) and settings.effective_mode != "offline"
    ra = ResearchAssistant(settings)
    result = ra.extract_announcement(
        text, title=args.title, symbol=args.symbol, prefer_live=prefer_live
    )
    meta = [f"模式：{settings.effective_mode}"]
    if result.verification:
        meta.append("已与财报交叉校验")
    files = export_assistant(
        f"公告要点：{args.title}", result.text, settings.output_dir,
        _formats(settings, args), verification=result.verification, meta_lines=meta,
    )
    print("\n" + "=" * 64)
    print("公告要点提炼完成")
    if result.verification:
        v = result.verification
        print(f"公告↔财报交叉校验：{'通过' if v.get('passed') else '未通过'}｜"
              f"一致 {v.get('supported', 0)}、矛盾 {v.get('mismatch', 0)}")
    for path in files:
        print("已生成：", path)
    print("=" * 64)
    return 0


def cmd_industry(args) -> int:
    settings = _settings(args)
    name = " ".join(args.name)
    ra = ResearchAssistant(settings)
    result = ra.summarize_industry(name)
    stats = result.extra.get("stats", {})
    files = export_assistant(
        f"{name} 行业汇总", result.text, settings.output_dir, _formats(settings, args),
        meta_lines=[f"成分股：{stats.get('count', '-')} 家", f"模式：{settings.effective_mode}"],
    )
    print("\n" + "=" * 64)
    print(f"{name} 行业汇总完成")
    for path in files:
        print("已生成：", path)
    print("=" * 64)
    return 0


def cmd_report(args) -> int:
    logger = get_logger("cli.report")
    settings = _settings(args)
    topic = " ".join(args.topic)
    formats = _formats(settings, args)
    orchestrator = ResearchOrchestrator(settings)
    report = orchestrator.run(topic, deep=args.deep)
    files = export_report(report, settings.output_dir, formats)
    print("\n" + "=" * 64)
    print(f"研报：{report.topic}｜章节 {len(report.sections)}｜参考资料 {len(report.references)}")
    for path in files:
        print("已生成：", path)
    print("=" * 64)
    logger.info("通用研报完成")
    return 0


def cmd_demo(args) -> int:
    settings = _settings(args)
    ra = ResearchAssistant(settings)

    print("\n########## 案例 1：公司投研速览（离线样例，全流程）##########")
    result = ra.company_brief("000000", prefer_live=False)
    v = result.verification or {}
    print(result.text)
    print(f"\n[交叉校验] 一致 {v.get('supported')}、矛盾 {v.get('mismatch')}、"
          f"幻觉率 {v.get('hallucination_rate', 0):.1%}")

    print("\n########## 案例 2：反幻觉演示（同一事实包，故意写错数字）##########")
    bundle = DataService().get_company_bundle("000000", prefer_live=False)
    fact_base = FactBase(bundle)
    bad = ("公司2025年营业总收入182.30亿元，同比增长12.50%；归母净利润31.80亿元，"
           "同比增长15.20%；销售毛利率50.00%，净资产收益率15.60%。")
    report = CrossVerifier().verify(bad, fact_base)
    print(report.render_text())

    print("\n########## 案例 3：公告要点提炼（公告↔财报交叉校验）##########")
    ann = ra.extract_announcement(
        sample_data.sample_announcement_text(), title="2025年年度业绩快报",
        symbol="000000", prefer_live=False,
    )
    av = ann.verification or {}
    print(f"[公告交叉校验] 一致 {av.get('supported')}、矛盾 {av.get('mismatch')}")

    files = export_assistant(
        "演示样本公司 投研速览（案例）", result.text, settings.output_dir,
        _formats(settings, args), verification=result.verification, fact_pack=result.fact_pack,
        meta_lines=["离线案例", "数据为虚构样例"],
    )
    print("\n案例产物：")
    for path in files:
        print("已生成：", path)
    return 0


def cmd_compare(args) -> int:
    settings = _settings(args)
    from src.analysis.comparison import ComparisonService
    from src.export.exporter import export_assistant

    prefer_live = (not args.sample) and settings.effective_mode != "offline"
    svc = ComparisonService()
    rows = svc.compare(args.symbols, prefer_live=prefer_live)
    md = svc.render_markdown(rows, by=args.by)

    formats = _formats(settings, args)
    files = export_assistant(
        f"多公司对比_{len(rows)}家", md, settings.output_dir, formats,
        meta_lines=[f"排序维度：{args.by}", f"模式：{settings.effective_mode}"],
    )
    print("\n" + "=" * 64)
    print(md)
    for path in files:
        print("已生成：", path)
    print("=" * 64)
    return 0


def cmd_serve(args) -> int:
    """启动 FastAPI Web 界面。"""
    settings = _settings(args)
    try:
        import uvicorn
    except ImportError:
        print("请先安装：pip install fastapi uvicorn")
        return 1
    from web.app import app
    print("启动 Web 服务：http://localhost:8000 （Ctrl+C 退出）")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
    return 0


def cmd_daily(args) -> int:
    """自选股批量跑 + 推送（配合 GitHub Actions 定时任务使用）。"""
    logger = get_logger("cli.daily")
    settings = _settings(args)

    # 读取自选股列表
    watchlist_file = Path(args.file)
    if not watchlist_file.exists():
        print(f"自选股列表文件不存在：{watchlist_file}")
        return 1

    symbols = []
    for line in watchlist_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            symbols.append(line)

    if not symbols:
        print("自选股列表为空，请在 watchlist.txt 里添加股票代码")
        return 1

    print(f"开始批量处理 {len(symbols)} 只自选股：{', '.join(symbols)}")
    print("=" * 64)

    ra = ResearchAssistant(settings)
    success, fail = 0, 0

    for symbol in symbols:
        try:
            print(f"\n[{symbol}] 处理中...")
            result = ra.company_brief(symbol, prefer_live=True)
            v = result.verification or {}

            # 推送
            if args.push:
                _push_brief(f"{symbol} 投研速览", result.text, v, settings)

            status = "通过" if v.get("passed") else "未通过"
            print(f"[{symbol}] 完成：校验{status}，幻觉率 {v.get('hallucination_rate', 0):.1%}")
            success += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[{symbol}] 失败：{exc}")
            logger.warning("处理 %s 失败：%s", symbol, exc)
            fail += 1

    print("\n" + "=" * 64)
    print(f"批量任务完成：成功 {success}，失败 {fail}")
    return 0 if fail == 0 else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 向后兼容：未写子命令时，默认按 report 处理
    if argv and argv[0] not in _COMMANDS and not argv[0].startswith("-"):
        argv = ["report"] + argv

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "brief": cmd_brief,
        "announce": cmd_announce,
        "industry": cmd_industry,
        "report": cmd_report,
        "compare": cmd_compare,
        "demo": cmd_demo,
        "serve": cmd_serve,
        "daily": cmd_daily,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        get_logger("main").error("执行失败：%s", exc)
        get_logger("main").error("可加 --sample / --mode offline 先跑离线案例排查。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
