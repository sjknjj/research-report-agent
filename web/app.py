"""FastAPI Web 服务（3.0升级）：SSE实时进度推送 + 异步任务。

启动方式：
    pip install fastapi uvicorn
    python main.py serve
    # 打开 http://localhost:8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("web.app")

# 下面的 import 必须在 sys.path 调整之后，故忽略 E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from src.agent.orchestrator import ResearchOrchestrator  # noqa: E402
from src.common.config import get_settings  # noqa: E402
from src.export.exporter import export_assistant, export_report  # noqa: E402
from src.services.research_assistant import ResearchAssistant  # noqa: E402

app = FastAPI(title="研报辅助 Agent 3.9", version="3.9")

# ---------------------------------------------------------------------------
# Web「设置」页的持久化配置
#
# 旧版本这里只有一个纯前端提示（saveSettings 什么都没保存），属于虚假功能。
# 现在真正落盘到 output/web_settings.json，并在启动时写入进程环境变量，
# 使 notify / monitor 等模块读取到用户配置。
# ---------------------------------------------------------------------------
WEB_SETTINGS_PATH = PROJECT_ROOT / "output" / "web_settings.json"

# 环境变量名 -> 前端字段名（带 env 前缀的字段会同步到 os.environ）
_WEB_SETTINGS_KEYS = {
    "feishu_webhook_url": "FEISHU_WEBHOOK_URL",
    "feishu_secret": "FEISHU_SECRET",
    "wecom_webhook_key": "WECOM_WEBHOOK_KEY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
}
_SECRET_FIELDS = {"feishu_secret", "wecom_webhook_key", "telegram_bot_token"}


def _load_web_settings() -> Dict[str, str]:
    if not WEB_SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(WEB_SETTINGS_PATH.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取 Web 设置失败：%s", exc)
        return {}


def _apply_web_settings(values: Dict[str, str]) -> None:
    """把设置写进进程环境变量，让 notify / monitor 等模块直接生效。"""
    for field, env_name in _WEB_SETTINGS_KEYS.items():
        value = values.get(field)
        if value:
            os.environ[env_name] = str(value)


def _save_web_settings(values: Dict[str, str]) -> None:
    WEB_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = _load_web_settings()
    merged.update({k: v for k, v in values.items() if v is not None})
    WEB_SETTINGS_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _apply_web_settings(merged)


# 启动即生效（命令行/邮件/webhook 等渠道共用同一份配置）
_apply_web_settings(_load_web_settings())


_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>研报辅助 Agent 3.9</title>
<!-- Markdown 渲染 + HTML 消毒（LLM/新闻内容属于外部输入，必须先消毒再插入 DOM） -->
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
<!-- 多公司对比图表（雷达图 / 估值分位）用 -->
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
*{box-sizing:border-box;}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
background:#f0f2f5;color:#1f2329;max-width:900px;margin:30px auto;padding:0 20px;}
h1{font-size:26px;margin:0 0 4px;}
.subtitle{color:#6b7280;font-size:14px;margin-bottom:24px;}
.version{font-size:12px;color:#0f4c81;background:#e6f0fa;
padding:2px 8px;border-radius:4px;margin-left:8px;}
.card{background:#fff;border-radius:12px;padding:24px;margin:16px 0;
box-shadow:0 1px 3px rgba(0,0,0,.06);}
input,button{font-size:15px;padding:10px 14px;border-radius:8px;border:1px solid #d0d5dd;}
input{flex:1;}
button{background:#0f4c81;color:#fff;border:none;cursor:pointer;margin-left:8px;}
button:hover{background:#0d3d6b;}
button:disabled{background:#999;cursor:not-allowed;}
button.secondary{background:#fff;color:#0f4c81;border:1px solid #0f4c81;}
.row{display:flex;gap:8px;margin:12px 0;align-items:center;}
.result{white-space:pre-wrap;margin-top:16px;line-height:1.8;font-size:14px;
max-height:500px;overflow-y:auto;padding:12px;background:#fafbfc;border-radius:8px;}

/* Markdown 渲染后的样式 */
.result h1 {font-size:22px;margin:16px 0 8px;color:#1f2329;}
.result h2 {font-size:18px;margin:14px 0 6px;color:#1f2329;}
.result h3 {font-size:16px;margin:12px 0 4px;color:#374151;}
.result p {margin:8px 0;}
.result ul, .result ol {margin:8px 0;padding-left:24px;}
.result li {margin:4px 0;}
.result table {border-collapse:collapse;width:100%;margin:12px 0;}
.result th, .result td {border:1px solid #e5e7eb;padding:8px;text-align:left;}
.result th {background:#f3f4f6;font-weight:600;}
.result code {background:#f3f4f6;padding:2px 6px;border-radius:4px;font-size:13px;}
.result pre {background:#f3f4f6;padding:12px;border-radius:8px;overflow-x:auto;}
.result blockquote {border-left:4px solid #d1d5db;padding-left:12px;margin:8px 0;color:#6b7280;}
.tip{color:#6b7280;font-size:13px;}
h3{margin:0 0 12px;font-size:17px;}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid #ccc;
border-top-color:#0f4c81;border-radius:50%;animation:spin .8s linear infinite;
vertical-align:middle;margin-right:6px;}
@keyframes spin{to{transform:rotate(360deg);}}
.watchlist{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0;}
.watch-item{background:#e6f0fa;color:#0f4c81;padding:6px 8px 6px 12px;
border-radius:6px;font-size:13px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;}
.watch-item:hover{background:#d0e3f5;}
.watch-del{display:inline-flex;align-items:center;justify-content:center;
width:16px;height:16px;line-height:1;border-radius:50%;font-size:14px;
color:#0f4c81;background:rgba(15,76,129,.12);cursor:pointer;flex:none;}
.watch-del:hover{background:#dc2626;color:#fff;}
.quote-alert{background:#fff3cd;border:1px solid #f59e0b;color:#b45309 !important;
padding:6px 10px;border-radius:6px;}
.tabs{display:flex;gap:0;border-bottom:1px solid #e5e7eb;margin-bottom:20px;}
.tab{padding:10px 20px;cursor:pointer;color:#6b7280;border-bottom:2px solid transparent;}
.tab.active{color:#0f4c81;border-bottom-color:#0f4c81;font-weight:500;}
.tab-content{display:none;}
.tab-content.active{display:block;}

/* 移动端适配（手机屏幕） */
@media (max-width: 768px) {
  body {
    margin: 10px auto;
    padding: 0 12px;
    font-size: 14px;
  }
  h1 {
    font-size: 20px;
  }
  .card {
    padding: 16px;
    margin: 12px 0;
  }
  .row {
    flex-direction: column;
    gap: 8px;
  }
  .row button {
    margin-left: 0;
    width: 100%;
  }
  .tabs {
    overflow-x: auto;
  }
  .tab {
    padding: 8px 12px;
    font-size: 13px;
    white-space: nowrap;
  }
  .result {
    max-height: 400px;
    font-size: 13px;
  }
}
</style></head><body>
<h1>研报辅助 Agent <span class="version">v3.9</span></h1>
<div class="subtitle">最靠谱的反幻觉 A 股投研助手</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('brief')">🏠 首页</div>
  <div class="tab" onclick="switchTab('compare')">📊 多公司对比</div>
  <div class="tab" onclick="switchTab('industry')">🏭 行业分析</div>
  <div class="tab" onclick="switchTab('report')">📝 深度研报</div>
  <div class="tab" onclick="switchTab('announce')">📢 公告提炼</div>
  <div class="tab" onclick="switchTab('sentiment')">😊 情绪面</div>
  <div class="tab" onclick="switchTab('backtest')">📈 回测</div>
  <div class="tab" onclick="switchTab('factor')">⭐ 因子</div>
  <div class="tab" onclick="switchTab('stats')">📊 统计</div>
  <div class="tab" onclick="switchTab('watchlist')">⭐ 自选股</div>
  <div class="tab" onclick="switchTab('daily')">📅 每日跑批</div>
  <div class="tab" onclick="switchTab('apikey')">🔑 API配置</div>
  <div class="tab" onclick="switchTab('settings')">⚙️ 设置</div>
</div>

<!-- 公司速览 Tab（首页） -->
<div id="tab-brief" class="tab-content active">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">输入股票代码，一键生成投研速览</h3>
    <div class="row" style="max-width:600px;margin:0 auto;">
      <input id="symbol" placeholder="如 600519（贵州茅台）" value="600519" style="font-size:16px;padding:14px;">
      <button onclick="startBrief()" style="font-size:16px;padding:14px 24px;">生成速览</button>
    </div>

    <!-- 数据源选择（全部展示，用户随便选） -->
    <div style="margin:20px 0;padding:12px;background:#f8f9fa;border-radius:8px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:#374151;">
        📊 数据源选择（可多选，自动容灾 + 交叉验证）
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="akshare" checked> AKShare（推荐）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="eastmoney_http"> 东方财富
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="sina_http" checked> 新浪财经
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="tencent_http" checked> 腾讯财经
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="tushare"> Tushare（配Token可用）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="wind"> Wind（装终端可用）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="choice"> Choice（装终端可用）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="ifind"> iFinD（装终端可用）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="jqdata"> JQData（配账号可用）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="ricequant"> RiceQuant（配账号可用）
        </label>
      </div>
      <div style="font-size:12px;color:#6b7280;margin-top:8px;">
        💡 选多个数据源会自动交叉验证，同一个数字不一致就标记"数据存疑"
      </div>
    </div>

    <div class="tip" style="text-align:center;">行情 + 财务 + 技术面 + 反幻觉校验 + 多空辩论 + 决策仪表盘</div>

    <!-- 报告操作按钮（结果出来后显示） -->
    <div id="brief_actions" style="display:none;gap:8px;margin:12px 0;padding:12px;background:#f8f9fa;border-radius:8px;">
      <button class="secondary" onclick="downloadPdf()" style="font-size:13px;padding:6px 12px;">📄 导出 PDF</button>
      <button class="secondary" onclick="addToWatchlist()" style="font-size:13px;padding:6px 12px;">⭐ 加入自选</button>
      <button class="secondary" id="autoRefreshBtn" onclick="toggleAutoRefresh()" style="font-size:13px;padding:6px 12px;">🔄 自动刷新</button>
    </div>
    <!-- 自动刷新只更新行情快照，不会重新调用大模型 -->
    <div id="quote_line" style="font-size:14px;color:#0f4c81;margin:6px 0;font-weight:600;"></div>
    <div id="autoRefreshStatus" style="font-size:12px;color:#6b7280;margin-top:4px;"></div>

    <div id="brief_result" class="result" style="display:none;"></div>
  </div>
</div>

<!-- 多公司对比 Tab -->
<div id="tab-compare" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">输入多个股票代码，横向对比</h3>
    <div class="row" style="max-width:600px;margin:0 auto;">
      <input id="compare_symbols" placeholder="如 600519,000858,000568" value="600519,000858,000568" style="font-size:16px;padding:14px;">
      <button onclick="startCompare()" style="font-size:16px;padding:14px 24px;">开始对比</button>
    </div>
    <div class="tip" style="text-align:center;margin-top:8px;">PE/PB/增速/ROE/技术面横向对比，自动排序</div>
    <div id="compare_result" class="result" style="display:none;"></div>
  </div>
  <div id="compare_charts"></div>
</div>

<!-- 行业分析 Tab -->
<div id="tab-industry" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">输入行业名称，自动抓取成分股</h3>
    <div class="row" style="max-width:600px;margin:0 auto;">
      <input id="industry_name" placeholder="如 银行/半导体/锂电池" value="银行" style="font-size:16px;padding:14px;">
      <button onclick="startIndustry()" style="font-size:16px;padding:14px 24px;">开始分析</button>
    </div>
    <div class="tip" style="text-align:center;margin-top:8px;">自动抓取行业成分股 + 板块涨跌聚合</div>
    <div id="industry_result" class="result" style="display:none;"></div>
  </div>
</div>

<!-- 深度研报 Tab -->
<div id="tab-report" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">输入研究主题，生成完整深度研报</h3>
    <div class="row" style="max-width:600px;margin:0 auto;">
      <input id="topic" placeholder="如 低空经济产业链分析" style="font-size:16px;padding:14px;">
      <button onclick="startReport()" style="font-size:16px;padding:14px 24px;">生成研报</button>
    </div>
    <div class="row" style="max-width:600px;margin:12px auto 0;">
      <label class="tip" style="margin:0;font-size:13px;"><input type="checkbox" id="deep"> 递归深挖检索（更慢但更全）</label>
    </div>

    <!-- 搜索引擎选择：只有深度研报会用到检索，所以放在这里并真正生效 -->
    <div style="margin:16px 0;padding:12px;background:#f8f9fa;border-radius:8px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:#374151;">
        🔍 搜索引擎（多选时按顺序尝试；国内推荐百度/必应，不用配 key）
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="search_engine" value="baidu" checked> 百度（推荐）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="search_engine" value="bing"> 必应
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="search_engine" value="duckduckgo"> DuckDuckGo（国内被墙）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="search_engine" value="tavily"> Tavily（需配 key）
        </label>
      </div>
      <div style="font-size:12px;color:#6b7280;margin-top:8px;">
        💡 勾选多个时，按「百度 → 必应 → DuckDuckGo → Tavily」的顺序逐级回退
      </div>
    </div>

    <div class="tip">流程：规划 → 检索 → 逐章分析 → 多空辩论 → 撰写 → 审校（并行生成）</div>
    <div id="report_progress" style="margin-top:16px;font-size:13px;color:#6b7280;"></div>
    <div id="report_result" class="result" style="display:none;"></div>
  </div>
</div>

<!-- 公告提炼 Tab -->
<div id="tab-announce" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">粘贴公告文本，自动提炼要点</h3>
    <div class="row" style="max-width:800px;margin:0 auto;">
      <textarea id="announce_text" placeholder="粘贴公告文本..." style="flex:1;height:200px;padding:12px;border-radius:8px;border:1px solid #d0d5dd;font-size:14px;"></textarea>
    </div>
    <div class="row" style="max-width:800px;margin:12px auto 0;">
      <input id="announce_symbol" placeholder="关联股票代码（可选）" style="flex:1;">
      <button onclick="startAnnounce()" style="padding:12px 24px;">提炼要点</button>
    </div>
    <div class="tip" style="text-align:center;margin-top:8px;">自动提炼要点 + 跟财报交叉校验数字</div>
    <div id="announce_result" class="result" style="display:none;"></div>
  </div>
</div>

<!-- 情绪面分析 Tab -->
<div id="tab-sentiment" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">新闻情绪分析</h3>
    <div class="row" style="max-width:600px;margin:0 auto;">
      <input id="sentiment_symbol" placeholder="股票代码，如 600519" value="600519">
      <button onclick="startSentiment()" style="padding:12px 24px;">分析情绪</button>
    </div>
    <div class="tip" style="text-align:center;margin-top:8px;">自动分析最近新闻情绪（正面/负面/中性）</div>
    <div id="sentiment_result" class="result" style="display:none;"></div>
  </div>
</div>

<!-- 回测 Tab -->
<div id="tab-backtest" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">MACD策略回测</h3>
    <div class="row" style="max-width:600px;margin:0 auto;">
      <input id="backtest_symbol" placeholder="股票代码，如 600519" value="600519">
      <button onclick="startBacktest()" style="padding:12px 24px;">开始回测</button>
    </div>
    <div class="tip" style="text-align:center;margin-top:8px;">回测MACD金叉死叉策略，看胜率</div>
    <div id="backtest_result" class="result" style="display:none;"></div>
  </div>
</div>

<!-- 因子分析 Tab -->
<div id="tab-factor" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">4因子打分</h3>
    <div class="row" style="max-width:600px;margin:0 auto;">
      <input id="factor_symbol" placeholder="股票代码，如 600519" value="600519">
      <button onclick="startFactor()" style="padding:12px 24px;">开始分析</button>
    </div>
    <div class="tip" style="text-align:center;margin-top:8px;">估值/成长/盈利/动量 4因子打分</div>
    <div id="factor_result" class="result" style="display:none;"></div>
  </div>
</div>

<!-- 统计 Tab -->
<div id="tab-stats" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">幻觉率历史统计</h3>
    <div style="text-align:center;">
      <button onclick="loadStats()" style="padding:12px 24px;">查看统计</button>
    </div>
    <div id="stats_result" class="result" style="display:none;"></div>
  </div>
</div>

<!-- 自选股 Tab -->
<div id="tab-watchlist" class="tab-content">
  <div class="card">
    <h3>我的自选股（本地存储）</h3>
    <div class="tip">点击股票代码快速生成速览</div>
    <div id="watchlist" class="watchlist"></div>
    <div id="watchlist_empty" class="tip" style="margin-top:20px;">
      还没有自选股，在「公司速览」页输入代码后点「+ 加入自选」
    </div>
  </div>
</div>

<!-- 每日跑批教程 Tab -->
<div id="tab-daily" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:20px;">📅 每日自动跑批配置教程</h3>
    <div style="max-width:700px;margin:0 auto;line-height:1.8;font-size:14px;">
      <p style="color:#6b7280;margin-bottom:20px;">配好之后，<strong>每个工作日早上 9 点自动生成自选股报告并推送到飞书/企微</strong>，不用你每天手动点。</p>

      <h4 style="color:#374151;margin:20px 0 10px;">第一步：把代码推到你的 GitHub 仓库</h4>
      <p>如果你还没有自己的仓库，先 Fork 一份：<br>
      👉 打开 <a href="https://github.com/sjknjj/research-report-agent" target="_blank">https://github.com/sjknjj/research-report-agent</a><br>
      👉 点右上角「Fork」按钮，复制到你自己的账号下</p>

      <h4 style="color:#374151;margin:20px 0 10px;">第二步：在 GitHub 仓库里配密钥（Secrets）</h4>
      <p>1. 打开你 Fork 的仓库 → 点「Settings」<br>
      2. 左边菜单点「Secrets and variables」→「Actions」<br>
      3. 点绿色的「New repository secret」按钮<br>
      4. 逐个添加以下密钥（名称要完全一样）：</p>
      <div style="background:#f8f9fa;padding:16px;border-radius:8px;margin:12px 0;font-family:monospace;font-size:13px;">
        <p><strong>LLM_BASE_URL</strong> = https://api.deepseek.com/v1</p>
        <p><strong>LLM_API_KEY</strong> = sk-你的大模型key</p>
        <p><strong>LLM_MODEL</strong> = deepseek-chat</p>
        <p><strong>FEISHU_WEBHOOK_URL</strong> = https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx</p>
      </div>
      <p style="font-size:13px;color:#6b7280;">💡 用企业微信的话，加 <strong>WECOM_WEBHOOK_KEY</strong> 而不是飞书的。</p>

      <h4 style="color:#374151;margin:20px 0 10px;">第三步：编辑自选股列表</h4>
      <p>在仓库根目录找到 <code>watchlist.txt</code>，一行一个股票代码：</p>
      <div style="background:#f8f9fa;padding:16px;border-radius:8px;margin:12px 0;font-family:monospace;font-size:13px;">
        <p>600519</p>
        <p>000858</p>
        <p>300750</p>
      </div>

      <h4 style="color:#374151;margin:20px 0 10px;">第四步：手动测试一次</h4>
      <p>1. 仓库页点「Actions」标签<br>
      2. 左边选「Daily Report」<br>
      3. 右边点「Run workflow」按钮<br>
      4. 等 1-2 分钟，看飞书群里有没有收到报告</p>

      <div style="background:#ecfdf5;border:1px solid #a7f3d0;padding:16px;border-radius:8px;margin-top:20px;">
        <p style="color:#059669;font-weight:600;">✅ 配好之后就不用管了，每天早上自动收到报告。</p>
      </div>
    </div>
  </div>
</div>

<!-- API 配置教程 Tab -->
<div id="tab-apikey" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:20px;">🔑 大模型 API Key 配置教程</h3>
    <div style="max-width:700px;margin:0 auto;line-height:1.8;font-size:14px;">
      <p style="color:#6b7280;margin-bottom:20px;">这个项目需要一个大模型 API 来生成研报。推荐用 DeepSeek（便宜好用），或者 OpenAI / 通义千问 / 智谱都行。</p>

      <h4 style="color:#374151;margin:20px 0 10px;">第一步：申请 API Key</h4>
      <p><strong>推荐 DeepSeek（便宜，1块钱能用好久）：</strong><br>
      👉 打开 <a href="https://platform.deepseek.com/" target="_blank">https://platform.deepseek.com/</a><br>
      👉 注册账号 → 充 10 块钱 → 创建 API Key → 复制下来</p>

      <p><strong>其他选项：</strong></p>
      <ul>
        <li>OpenAI：https://platform.openai.com/</li>
        <li>通义千问（阿里云）：https://dashscope.aliyun.com/</li>
        <li>智谱AI：https://open.bigmodel.cn/</li>
      </ul>

      <h4 style="color:#374151;margin:20px 0 10px;">第二步：填到 .env 文件里</h4>
      <p>在项目根目录找到 <code>.env.example</code> 文件，复制一份改名叫 <code>.env</code>，然后填上你的 key：</p>
      <div style="background:#f8f9fa;padding:16px;border-radius:8px;margin:12px 0;font-family:monospace;font-size:13px;">
        <p># 大模型配置（推荐 DeepSeek）</p>
        <p>LLM_BASE_URL=https://api.deepseek.com/v1</p>
        <p>LLM_API_KEY=sk-你的key填在这里</p>
        <p>LLM_MODEL=deepseek-chat</p>
      </div>

      <h4 style="color:#374151;margin:20px 0 10px;">第三步：保存并重启</h4>
      <p>保存 <code>.env</code> 文件，然后重启 Web 服务：</p>
      <div style="background:#f8f9fa;padding:16px;border-radius:8px;margin:12px 0;font-family:monospace;font-size:13px;">
        <p># 如果用 Docker：</p>
        <p>docker-compose restart</p>
        <p>&nbsp;</p>
        <p># 如果用本地 Python：</p>
        <p># 按 Ctrl+C 停掉，然后重新跑：</p>
        <p>python main.py serve</p>
      </div>

      <h4 style="color:#374151;margin:20px 0 10px;">第四步：测试一下</h4>
      <p>打开浏览器 <code>http://localhost:8000</code>，在首页输入股票代码（比如 600519），点「生成速览」，能出结果就说明配好了。</p>

      <div style="background:#ecfdf5;border:1px solid #a7f3d0;padding:16px;border-radius:8px;margin-top:20px;">
        <p style="color:#059669;font-weight:600;">💡 不用大模型 API 也能跑：不配 key 就用离线样例模式，能走通流程但不能生成真研报。</p>
      </div>
    </div>
  </div>
</div>

<!-- 设置 Tab -->
<div id="tab-settings" class="tab-content">
  <div class="card">
    <h3 style="text-align:center;font-size:20px;margin-bottom:16px;">⚙️ 系统设置</h3>

    <div style="margin-bottom:24px;">
      <h4 style="margin-bottom:12px;color:#374151;">📬 推送渠道（配好后每日自动推送报告）</h4>
      <div style="padding:16px;background:#f8f9fa;border-radius:8px;">
        <div style="margin-bottom:12px;">
          <label style="display:block;font-size:13px;font-weight:600;margin-bottom:4px;">飞书 Webhook 地址</label>
          <input id="feishu_webhook" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx" style="width:100%;">
        </div>
        <div style="margin-bottom:12px;">
          <label style="display:block;font-size:13px;font-weight:600;margin-bottom:4px;">企业微信 Webhook Key</label>
          <input id="wecom_key" placeholder="你的webhook key" style="width:100%;">
        </div>
        <button onclick="saveSettings()" style="padding:8px 16px;">💾 保存设置</button>
        <div id="settings_status" style="font-size:13px;color:#6b7280;margin-top:8px;"></div>
      </div>
    </div>

    <div style="margin-bottom:24px;">
      <h4 style="margin-bottom:12px;color:#374151;">🔔 盘中预警（达到涨跌幅自动推送）</h4>
      <div style="padding:16px;background:#f8f9fa;border-radius:8px;">
        <div style="display:flex;gap:16px;">
          <div style="flex:1;">
            <label style="display:block;font-size:13px;font-weight:600;margin-bottom:4px;">涨幅预警阈值（%）</label>
            <input id="alert_up" type="number" value="5" style="width:100%;">
          </div>
          <div style="flex:1;">
            <label style="display:block;font-size:13px;font-weight:600;margin-bottom:4px;">跌幅预警阈值（%）</label>
            <input id="alert_down" type="number" value="-5" style="width:100%;">
          </div>
        </div>
      </div>
    </div>

    <div>
      <h4 style="margin-bottom:12px;color:#374151;">ℹ️ 关于</h4>
      <div style="padding:16px;background:#f8f9fa;border-radius:8px;font-size:13px;color:#6b7280;">
        <p>研报辅助 Agent v3.9</p>
        <p>反幻觉三道防线 · 12个数据源 · 多源交叉验证</p>
        <p>GitHub: https://github.com/sjknjj/research-report-agent</p>
      </div>
    </div>
  </div>
</div>

<script>
// 安全的 Markdown 渲染：报告正文含大模型输出与新闻标题（外部输入），
// 直接 marked.parse + innerHTML 会被注入 <script>/<img onerror> 等，必须消毒。
function renderMD(src){
  const html = marked.parse(src || '');
  if (window.DOMPurify) {
    return DOMPurify.sanitize(html, {USE_PROFILES:{html:true}});
  }
  // 兜底：没有加载到 DOMPurify 时退化为纯文本，绝不直接插入未消毒 HTML
  const div=document.createElement('div');
  div.textContent = src || '';
  return div.innerHTML;
}
function escapeHTML(s){
  return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function switchTab(name){
  document.querySelectorAll('.tab').forEach(t=>{
    t.classList.toggle('active', t.getAttribute('onclick').includes("'"+name+"'"));
  });
  document.querySelectorAll('.tab-content').forEach((c)=>c.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  if(name==='settings'){ loadSettings(); }
}

// 自选股本地存储
function getWatchlist(){
  try { return JSON.parse(localStorage.getItem('watchlist')||'[]'); }
  catch(e){ return []; }
}
function setWatchlist(list){
  localStorage.setItem('watchlist', JSON.stringify(list));
  renderWatchlist();
}
function addToWatchlist(){
  const sym=document.getElementById('symbol').value.trim();
  if(!sym) return;
  const list=getWatchlist();
  if(!list.includes(sym)){
    list.push(sym);
    setWatchlist(list);
    alert('已加入自选股：'+sym);
  }
}
function removeFromWatchlist(sym){
  // 从 localStorage 中移除后立即重渲染（列表为空会自动显示空状态）
  setWatchlist(getWatchlist().filter(s=>s!==sym));
}
function renderWatchlist(){
  const list=getWatchlist();
  const el=document.getElementById('watchlist');
  const empty=document.getElementById('watchlist_empty');
  el.innerHTML='';
  if(list.length===0){empty.style.display='block';return;}
  empty.style.display='none';
  // 之前的模板字符串把 localStorage 内容直接拼进 onclick，含引号即可注入脚本；
  // 这里继续用 createElement + addEventListener 的写法，避免任何 HTML 字符串拼接。
  list.forEach(sym=>{
    const span=document.createElement('span');
    span.className='watch-item';
    span.title='点击生成速览';
    span.textContent=sym;
    span.addEventListener('click', ()=>quickBrief(sym));

    const del=document.createElement('span');
    del.className='watch-del';
    del.title='从自选股移除';
    del.setAttribute('role','button');
    del.setAttribute('aria-label','从自选股移除 '+sym);
    del.textContent='×';
    del.addEventListener('click', (ev)=>{
      ev.stopPropagation();  // 不要连带触发外层的“生成速览”
      removeFromWatchlist(sym);
    });

    span.appendChild(del);
    el.appendChild(span);
  });
}
renderWatchlist();
loadAlertThresholds();  // 页面加载时拉取一次预警阈值

async function quickBrief(sym){
  document.getElementById('symbol').value=sym;
  switchTab('brief');
  startBrief();
}

async function startBrief(){
  const sym=document.getElementById('symbol').value.trim();
  // 收集选中的数据源
  const sources=Array.from(document.querySelectorAll('input[name="datasource"]:checked'))
    .map(cb=>cb.value).join(',');
  const el=document.getElementById('brief_result');
  el.style.display='block';
  el.innerHTML='<span class="spinner"></span>抓取数据 + 生成分析 + 交叉校验中…';
  try{
    const r=await fetch('/api/brief?symbol='+encodeURIComponent(sym)+'&sources='+encodeURIComponent(sources));
    const d=await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.innerHTML = renderMD(d.text || '');
    if(d.verification && d.verification.passed !== undefined){
      const v=d.verification;
      // T1：三态结论，覆盖率不足时明确显示"通过但有保留"
      const mark = v.verdict==='pass' ? '✅' : (v.verdict==='fail' ? '❌' : '⚠️');
      const text = v.verdict_text || (v.passed?'通过':'未通过');
      const cov = (v.coverage!==undefined) ? (v.coverage*100).toFixed(0)+'%' : '—';
      el.innerHTML += `<hr><p style="color:#6b7280;font-size:13px;">交叉校验：${mark} ${escapeHTML(text)}｜一致${v.supported}、矛盾${v.mismatch}、无来源${v.unsourced||0}、覆盖率${cov}、幻觉率${(v.hallucination_rate*100).toFixed(1)}%</p>`;
      if(v.verdict_reasons && v.verdict_reasons.length){
        el.innerHTML += `<ul style="color:#b45309;font-size:13px;">${v.verdict_reasons.map(r=>'<li>'+escapeHTML(r)+'</li>').join('')}</ul>`;
      }
    }
    // 显示操作按钮
    document.getElementById('brief_actions').style.display = 'flex';
  }catch(e){el.textContent='请求失败：'+e;}
}

// 下载 PDF
function downloadPdf(){
  const sym = document.getElementById('symbol').value.trim();
  const sources = Array.from(document.querySelectorAll('input[name="datasource"]:checked'))
    .map(cb=>cb.value).join(',');
  window.open('/api/brief/pdf?symbol='+encodeURIComponent(sym)+'&sources='+encodeURIComponent(sources), '_blank');
}

// 多公司对比
async function startCompare(){
  const symbols = document.getElementById('compare_symbols').value.trim();
  if(!symbols){alert('请输入至少2个股票代码');return;}
  const el = document.getElementById('compare_result');
  const chartWrap = document.getElementById('compare_charts');
  el.style.display = 'block';
  el.innerHTML = '<span class="spinner"></span>抓取数据 + 横向对比中…';
  if(chartWrap){chartWrap.innerHTML='';}
  try{
    const r = await fetch('/api/compare?symbols='+encodeURIComponent(symbols));
    const d = await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.innerHTML = renderMD(d.text || '');
    renderCompareCharts(d.charts);
  }catch(e){el.textContent='请求失败：'+e;}
}

// 用 plotly.js 渲染后端返回的图表 JSON（雷达图 / 估值分位）
function renderCompareCharts(charts){
  const wrap = document.getElementById('compare_charts');
  if(!wrap || !charts || !window.Plotly){ return; }
  wrap.innerHTML = '';
  [['radar','多公司对比雷达图'],['valuation','估值分位图']].forEach(([key,title])=>{
    const fig = charts[key];
    if(!fig){ return; }
    const card = document.createElement('div');
    card.className = 'card';
    const h = document.createElement('h3');
    h.textContent = title;
    const plot = document.createElement('div');
    card.appendChild(h); card.appendChild(plot);
    wrap.appendChild(card);
    try{
      Plotly.newPlot(plot, fig.data || [], fig.layout || {}, {responsive:true, displaylogo:false});
    }catch(e){
      plot.textContent = '图表渲染失败：'+e;
    }
  });
}

// 公告提炼
async function startAnnounce(){
  const text = document.getElementById('announce_text').value.trim();
  const symbol = document.getElementById('announce_symbol').value.trim();
  if(!text){alert('请粘贴公告文本');return;}
  const el = document.getElementById('announce_result');
  el.style.display = 'block';
  el.innerHTML = '<span class="spinner"></span>提炼要点 + 交叉校验中…';
  try{
    const params = new URLSearchParams();
    params.append('text', text);
    if(symbol) params.append('symbol', symbol);
    const r = await fetch('/api/announce?'+params.toString(), {method:'POST'});
    const d = await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.innerHTML = renderMD(d.text || '');
    if(d.verification && d.verification.passed !== undefined){
      const v = d.verification;
      const mark = v.verdict==='pass' ? '✅' : (v.verdict==='fail' ? '❌' : '⚠️');
      const text = v.verdict_text || (v.passed?'通过':'未通过');
      const cov = (v.coverage!==undefined) ? (v.coverage*100).toFixed(0)+'%' : '—';
      el.innerHTML += `<hr><p style="color:#6b7280;font-size:13px;">交叉校验：${mark} ${escapeHTML(text)}｜无来源${v.unsourced||0}、覆盖率${cov}、幻觉率${(v.hallucination_rate*100).toFixed(1)}%</p>`;
    }
  }catch(e){el.textContent='请求失败：'+e;}
}

// 统计
async function loadStats(){
  const el = document.getElementById('stats_result');
  el.style.display = 'block';
  el.innerHTML = '<span class="spinner"></span>加载统计中…';
  try{
    const r = await fetch('/api/stats');
    const d = await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.innerHTML = renderMD(d.text || '');
  }catch(e){el.textContent='请求失败：'+e;}
}

// 情绪面分析
async function startSentiment(){
  const symbol = document.getElementById('sentiment_symbol').value.trim();
  if(!symbol){alert('请输入股票代码');return;}
  const el = document.getElementById('sentiment_result');
  el.style.display = 'block';
  el.innerHTML = '<span class="spinner"></span>分析新闻情绪中…';
  try{
    const r = await fetch('/api/sentiment?symbol='+encodeURIComponent(symbol));
    const d = await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.innerHTML = renderMD(d.text || '');
  }catch(e){el.textContent='请求失败：'+e;}
}

// 回测
async function startBacktest(){
  const symbol = document.getElementById('backtest_symbol').value.trim();
  if(!symbol){alert('请输入股票代码');return;}
  const el = document.getElementById('backtest_result');
  el.style.display = 'block';
  el.innerHTML = '<span class="spinner"></span>回测MACD策略中…';
  try{
    const r = await fetch('/api/backtest?symbol='+encodeURIComponent(symbol));
    const d = await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.innerHTML = renderMD(d.text || '');
  }catch(e){el.textContent='请求失败：'+e;}
}

// 因子分析
async function startFactor(){
  const symbol = document.getElementById('factor_symbol').value.trim();
  if(!symbol){alert('请输入股票代码');return;}
  const el = document.getElementById('factor_result');
  el.style.display = 'block';
  el.innerHTML = '<span class="spinner"></span>计算4因子打分中…';
  try{
    const r = await fetch('/api/factor?symbol='+encodeURIComponent(symbol));
    const d = await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.innerHTML = renderMD(d.text || '');
  }catch(e){el.textContent='请求失败：'+e;}
}

// 行业分析
async function startIndustry(){
  const name = document.getElementById('industry_name').value.trim();
  if(!name){alert('请输入行业名称');return;}
  const el = document.getElementById('industry_result');
  el.style.display = 'block';
  el.innerHTML = '<span class="spinner"></span>抓取行业成分股中…';
  try{
    const r = await fetch('/api/industry?name='+encodeURIComponent(name));
    const d = await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.innerHTML = renderMD(d.text || '');
  }catch(e){el.textContent='请求失败：'+e;}
}

// 保存设置（真正落盘到 output/web_settings.json）
async function saveSettings(){
  const status = document.getElementById('settings_status');
  const payload = {
    feishu_webhook_url: document.getElementById('feishu_webhook').value.trim(),
    wecom_webhook_key: document.getElementById('wecom_key').value.trim(),
    alert_up_pct: document.getElementById('alert_up').value,
    alert_down_pct: document.getElementById('alert_down').value,
  };
  status.textContent = '保存中…';
  status.style.color = '#6b7280';
  try{
    const r = await fetch('/api/settings', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if(d.ok){
      status.textContent = '✅ 已保存到 ' + d.config_file + '，推送渠道立即生效';
      status.style.color = '#059669';
      loadAlertThresholds();  // 阈值改动立即对行情预警生效
    } else {
      status.textContent = '保存失败：' + (d.detail || '未知错误');
      status.style.color = '#dc2626';
    }
  }catch(e){
    status.textContent = '保存失败：' + e;
    status.style.color = '#dc2626';
  }
}

// 打开设置页时回填已保存的值
async function loadSettings(){
  try{
    const r = await fetch('/api/settings');
    const d = await r.json();
    if(d.feishu_webhook_url) document.getElementById('feishu_webhook').value = d.feishu_webhook_url;
    if(d.wecom_webhook_key) document.getElementById('wecom_key').value = d.wecom_webhook_key;
    if(d.alert_up_pct) document.getElementById('alert_up').value = d.alert_up_pct;
    if(d.alert_down_pct) document.getElementById('alert_down').value = d.alert_down_pct;
  }catch(e){ /* 读取失败时保持空白即可 */ }
}

// 自动刷新：只拉取实时行情快照，不再重新跑整条大模型流水线
let autoRefreshTimer = null;
// 预警阈值前端缓存：页面加载 / 保存设置后从 /api/settings 刷新
let alertThresholds = {up: 5, down: 5};
async function loadAlertThresholds(){
  try{
    const r = await fetch('/api/settings');
    const d = await r.json();
    const up = parseFloat(d.alert_up_pct);
    const down = Math.abs(parseFloat(d.alert_down_pct));
    if(!isNaN(up)) alertThresholds.up = up;
    if(!isNaN(down)) alertThresholds.down = down;
  }catch(e){ /* 读取失败时沿用默认 5% */ }
}
async function refreshQuote(){
  const sym = document.getElementById('symbol').value.trim();
  const line = document.getElementById('quote_line');
  if(!sym || !line){ return; }
  try{
    const r = await fetch('/api/quote?symbol='+encodeURIComponent(sym));
    const d = await r.json();
    if(d.detail){ line.textContent = '行情刷新失败：'+d.detail; line.classList.remove('quote-alert'); return; }
    const pct = Number(d.pct_change) || 0;
    const sign = pct >= 0 ? '+' : '';
    const quote = (d.name||d.symbol)+' '+d.price+' 元  '+sign+pct+'%';
    // 涨幅 ≥ 上涨阈值，或跌幅 ≤ 下跌阈值（下跌阈值取绝对值）时给出醒目预警
    if(alertThresholds.up > 0 && pct >= alertThresholds.up){
      line.textContent = '⚡ 涨幅预警 '+sign+pct+'% 超过阈值 '+alertThresholds.up+'%｜'+quote;
      line.style.color = '#b45309';
      line.classList.add('quote-alert');
    } else if(alertThresholds.down > 0 && pct <= -alertThresholds.down){
      line.textContent = '⚡ 跌幅预警 '+pct+'% 超过阈值 -'+alertThresholds.down+'%｜'+quote;
      line.style.color = '#b45309';
      line.classList.add('quote-alert');
    } else {
      line.textContent = quote;
      line.style.color = pct >= 0 ? '#dc2626' : '#059669';
      line.classList.remove('quote-alert');
    }
  }catch(e){
    line.textContent = '行情刷新失败：'+e;
  }
}
function toggleAutoRefresh(){
  const btn = document.getElementById('autoRefreshBtn');
  const status = document.getElementById('autoRefreshStatus');
  if(autoRefreshTimer){
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
    btn.textContent = '🔄 自动刷新';
    status.textContent = '';
  } else {
    btn.textContent = '⏸ 停止刷新';
    status.textContent = '每 10 秒刷新一次实时行情（不会重复调用大模型）';
    refreshQuote();
    autoRefreshTimer = setInterval(refreshQuote, 10000);
  }
}

async function startReport(){
  const topic=document.getElementById('topic').value.trim();
  if(!topic){alert('请输入研究主题');return;}
  const deep=document.getElementById('deep').checked;
  // 把勾选的搜索引擎按优先级串起来，取第一个发给后端
  const engines=Array.from(document.querySelectorAll('input[name="search_engine"]:checked'))
    .map(cb=>cb.value);
  const provider = engines.length ? engines[0] : 'auto';
  const progEl=document.getElementById('report_progress');
  const resultEl=document.getElementById('report_result');
  resultEl.style.display='block';
  resultEl.innerHTML='';

  let stepLog=[];
  progEl.innerHTML='<span class="spinner"></span> 正在启动流水线…';

  const es=new EventSource('/api/report/stream?topic='+encodeURIComponent(topic)
    +'&deep='+deep+'&provider='+encodeURIComponent(provider));

  es.onmessage=function(e){
    const data=JSON.parse(e.data);
    if(data.type==='step'){
      stepLog.push(data.name);
      progEl.innerHTML='<span class="spinner"></span> 已完成：'+escapeHTML(stepLog.join(' → '));
    } else if(data.type==='done'){
      es.close();
      progEl.innerHTML='✅ 生成完成（共 '+stepLog.length+' 个阶段）';
      resultEl.innerHTML = renderMD(data.text || '');
    } else if(data.type==='error'){
      es.close();
      progEl.innerHTML='❌ 出错了';
      resultEl.textContent='错误：'+data.message;
    }
  };
  es.onerror=function(){es.close();};
}
</script></body></html>"""


class BriefResponse(BaseModel):
    text: str
    verification: dict = {}
    output_file: str = ""


class ReportResponse(BaseModel):
    text: str
    sections: int = 0
    references: int = 0
    output_file: str = ""


@app.get("/", response_class=HTMLResponse)
def index():
    return _PAGE


@app.get("/api/brief", response_model=BriefResponse)
def api_brief(symbol: str, sources: str = ""):
    settings = get_settings()
    # 把用户选中的数据源列表传给 ResearchAssistant；
    # 留空 = 交给配置/完备度顺序决定，而不是硬写死 akshare 单源
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    ra = ResearchAssistant(settings, sources=source_list)
    try:
        result = ra.company_brief(symbol, prefer_live=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))

    # 如实标注数据来源：旧版本无条件写"✅ 已启用多源交叉验证"，
    # 但多源管理器当时根本没有接线，属于虚假声明。
    used_source = "-"
    if isinstance(result.bundle, dict):
        used_source = str(result.bundle.get("source", "-"))
    source_info = f"\n\n---\n\n📊 **本次实际数据来源**：{used_source}"
    if source_list:
        source_info += f"（本次勾选：{', '.join(source_list)}）"
    source_info += "\n"

    conflict_text = _render_cross_source_conflicts(result)
    if conflict_text:
        source_info += conflict_text
    result.text = result.text + source_info

    files = export_assistant(
        f"{symbol} 投研速览", result.text, settings.output_dir,
        formats=("md", "html"),
        verification=result.verification,
        fact_pack=result.fact_pack,
        chart_html=_build_brief_charts(result),
        extra_html=result.extra.get("chain_graph", "") if result.extra else "",
    )
    return BriefResponse(
        text=result.text,
        verification=result.verification or {},
        output_file=str(files[0]) if files else "",
    )


def _build_brief_charts(result) -> str:
    """拼装公司速览的图表：K线 + 财务趋势 + PE/PB 估值仪表盘。

    这些图表此前在仓库里"定义了但没有任何调用点"，README 却宣称有。
    现在统一在这里生成（缓存命中也有效，因为 price_clean/financial_clean 会被还原）。
    """
    raw = getattr(result, "raw_bundle", None)
    if raw is None:
        return ""
    from src.export import charts as chart_mod

    parts: list = []
    try:
        if getattr(raw, "price_clean", None) is not None:
            parts.append(chart_mod.kline_with_volume(raw.price_clean) or "")
        if getattr(raw, "financial_clean", None) is not None:
            parts.append(chart_mod.financial_trend_chart(raw.financial_clean) or "")
        valuation = getattr(raw, "valuation", None) or {}
        parts.append(
            chart_mod.valuation_gauge(valuation.get("pe"), valuation.get("pb")) or ""
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("速览图表生成失败（不影响报告）：%s", exc)
    return "".join(p for p in parts if p)


def _render_cross_source_conflicts(result) -> str:
    """把多源交叉验证的实际结果渲染出来。

    只有在 DataService 真正跑了多源交叉验证并带回结果时才输出，
    没有结果就返回空字符串——不再凭空宣称"已启用交叉验证"。
    """
    extra = getattr(result, "extra", None) or {}
    check = extra.get("cross_check")
    if not check:
        return ""
    lines = ["\n🔎 **多源交叉验证结果**\n"]
    conflicts = check.get("conflicts") if isinstance(check, dict) else None
    if conflicts:
        for item in conflicts:
            lines.append(f"- ⚠️ 数据不一致：{item}")
    else:
        lines.append("- ✅ 所选数据源之间未发现数字冲突")
    return "\n".join(lines) + "\n"


@app.get("/api/brief/pdf")
def api_brief_pdf(symbol: str, sources: str = ""):
    """直接返回 PDF 文件，前端可以直接下载。"""
    import os
    from fastapi.responses import FileResponse

    settings = get_settings()
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    ra = ResearchAssistant(settings, sources=source_list)
    try:
        result = ra.company_brief(symbol, prefer_live=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))

    source_info = f"\n\n---\n\n📊 **本次使用的数据源**：{', '.join(source_list)}\n"
    result.text = result.text + source_info

    # 导出 PDF
    from src.export.pdf_export import export_pdf
    pdf_path = export_pdf(result.text, f"{symbol}_brief.pdf", title=f"{symbol} 投研速览")
    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=500, detail="PDF 导出失败")

    return FileResponse(
        path=pdf_path,
        filename=f"{symbol}_brief.pdf",
        media_type="application/pdf",
    )


@app.get("/api/compare")
def api_compare(symbols: str, by: str = "profit_yoy"):
    """多公司对比。symbols 是逗号分隔的股票代码，如 600519,000858,000568"""
    from src.analysis.comparison import (
        ComparisonService,
        radar_chart_html,
        radar_figure_json,
        valuation_figure_json,
        valuation_percentile_html,
    )

    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if len(symbol_list) < 2:
        raise HTTPException(status_code=400, detail="至少需要2个股票代码")

    settings = get_settings()
    try:
        service = ComparisonService()
        rows = service.compare(symbol_list, prefer_live=True)
        markdown = service.render_markdown(rows, by=by)

        # 图表：一份 JSON 给前端用 plotly.js 渲染，一份 HTML 写进导出文件
        charts = {
            "radar": radar_figure_json(rows),
            "valuation": valuation_figure_json(rows),
        }
        chart_html = ""
        try:
            chart_html = radar_chart_html(rows) + valuation_percentile_html(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("对比图表生成失败（不影响表格）：%s", exc)

        export_assistant(
            f"多公司对比_{len(rows)}家", markdown, settings.output_dir,
            formats=("md", "html"), chart_html=chart_html,
            meta_lines=[f"排序维度：{by}", f"公司数：{len(rows)}"],
        )
        return {"text": markdown, "count": len(rows), "charts": charts}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/announce")
def api_announce(text: str, symbol: str = "", title: str = "公司公告"):
    """公告提炼。"""
    from src.services.research_assistant import ResearchAssistant

    settings = get_settings()
    ra = ResearchAssistant(settings)
    try:
        result = ra.extract_announcement(text, title=title, symbol=symbol or None)
        return {"text": result.text, "verification": result.verification or {}}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/stats")
def api_stats():
    """幻觉率历史统计 - 接真实数据。"""
    from src.verify.stats import HallucinationStats

    try:
        stats = HallucinationStats()
        text = stats.render_markdown()
        return {"text": text}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/sentiment")
def api_sentiment(symbol: str):
    """情绪面分析 - 接真实数据。"""
    from src.services.research_assistant import ResearchAssistant
    from src.analysis.sentiment import analyze_sentiment

    settings = get_settings()
    ra = ResearchAssistant(settings)
    try:
        # 先获取公司的新闻
        bundle = ra.data.get_company_bundle(symbol, prefer_live=True)
        news_list = bundle.news[:20] if bundle.news else []

        if not news_list:
            return {"text": f"# {symbol} 情绪面分析\n\n暂无新闻数据，无法分析情绪。"}

        # analyze_sentiment 返回的是规范化结构：
        # overall / score / positive / negative / neutral / *_ratio / *_news / summary / analyzed
        result = analyze_sentiment(news_list, ra.llm)

        text = "".join([
            f"# {symbol} 情绪面分析\n\n",
            "## 新闻情绪概览\n\n",
            f"- **分析新闻数**：{result.get('news_count', len(news_list))} 篇\n",
            f"- **整体情绪**：{result.get('overall', '中性')}\n",
            f"- **情绪评分**：{result.get('score', 50):.1f} / 100\n",
            f"- **正面/负面/中性**：{result.get('positive', 0)} / "
            f"{result.get('negative', 0)} / {result.get('neutral', 0)} 篇\n",
            f"- **正面占比**：{result.get('positive_ratio', 0) * 100:.1f}%\n",
            f"- **负面占比**：{result.get('negative_ratio', 0) * 100:.1f}%\n\n",
            f"**一句话总结**：{result.get('summary', '—')}\n\n",
        ])

        if not result.get("analyzed", False):
            text += "> ⚠️ 本次未获得模型分析结果（可能处于离线演示模式），以上为中性占位。\n\n"

        text += "## 主要正面新闻\n\n"
        positive_news = result.get("positive_news", [])
        text += "".join(f"- {n.get('title', '')}\n" for n in positive_news[:5]) or "- 无\n"
        text += "\n## 主要负面新闻\n\n"
        negative_news = result.get("negative_news", [])
        text += "".join(f"- {n.get('title', '')}\n" for n in negative_news[:5]) or "- 无\n"

        # 模型没有逐条标注时，退回展示原始新闻标题，避免"主要新闻"永远为空
        if not positive_news and not negative_news:
            text += "\n## 最近新闻标题\n\n"
            text += "".join(
                f"- {n.get('time', '')} {n.get('title', '')}\n" for n in news_list[:5]
            )

        text += "\n> ⚠️ 情绪面分析仅供参考，不构成投资建议。\n"
        return {"text": text}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/backtest")
def api_backtest(symbol: str, days: int = 60):
    """回测功能 - 接真实数据。"""
    from src.services.research_assistant import ResearchAssistant
    from src.analysis.backtest import backtest_macd

    settings = get_settings()
    ra = ResearchAssistant(settings)
    try:
        # 注意：CompanyBundle 里清洗后的行情 DataFrame 字段名是 price_clean（不是 price_df）
        bundle = ra.data.get_company_bundle(symbol, prefer_live=True)
        price_df = getattr(bundle, "price_clean", None)

        if price_df is None or len(price_df) == 0:
            return {"text": f"# {symbol} 回测\n\n暂无K线数据，无法回测。"}

        price_df = price_df.tail(days)
        result = backtest_macd(price_df)

        if result.get("error"):
            return {"text": f"# {symbol} 回测\n\n{result['error']}。"}

        # backtest_macd 返回的比率已经是百分数，这里不能再 ×100
        def _pct(key: str) -> str:
            value = result.get(key)
            return "—" if value is None else f"{value:.2f}%"

        text = f"""# {symbol} MACD 策略回测

## 回测设置

- **回测周期**：最近 {len(price_df)} 个交易日
- **策略**：MACD 金叉买入 / 死叉卖出（信号次日成交）
- **初始资金**：100,000 元
- **交易成本**：买入佣金万2.5；卖出佣金万2.5 + 印花税千1

## 回测结果

| 指标 | 数值 |
|---|---|
| 完成交易次数 | {result.get('total_trades', 0)} 次 |
| 盈利次数 | {result.get('win_trades', 0)} 次 |
| 亏损次数 | {result.get('lose_trades', 0)} 次 |
| 胜率 | {_pct('win_rate')} |
| 平均单笔收益 | {_pct('avg_profit_pct')} |
| 策略累计收益 | {_pct('strategy_return_pct')} |
| 同期买入持有 | {_pct('buy_hold_return_pct')} |
| 最大回撤 | {_pct('max_drawdown_pct')} |

## 结论

{result.get('summary', '回测完成。')}

> ⚠️ 历史回测不代表未来收益，且未考虑滑点与流动性冲击，仅供参考。
"""
        return {"text": text}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/factor")
def api_factor(symbol: str):
    """因子分析 - 接真实数据。"""
    from src.services.research_assistant import ResearchAssistant
    from src.analysis.comparison import factor_analysis, CompanyRow

    settings = get_settings()
    ra = ResearchAssistant(settings)
    try:
        bundle = ra.data.get_company_bundle(symbol, prefer_live=True)

        # 字段名必须与 CompanyRow 的声明一致；财务指标在
        # financial_summary["latest"] 里，且键名带 _pct / _yuan 后缀。
        fin = bundle.financial_summary.get("latest", {}) or {}
        val = bundle.valuation or {}
        price = bundle.price_summary or {}
        tech = bundle.technicals or {}

        row = CompanyRow(
            symbol=bundle.symbol,
            name=bundle.name,
            pe=val.get("pe"),
            pb=val.get("pb"),
            revenue=fin.get("revenue_yuan"),
            net_profit=fin.get("net_profit_yuan"),
            revenue_yoy=fin.get("revenue_yoy_pct"),
            profit_yoy=fin.get("net_profit_yoy_pct"),
            gross_margin=fin.get("gross_margin_pct"),
            roe=fin.get("roe_pct"),
            period_return=price.get("period_return_pct"),
            trend=tech.get("trend", "") or "",
            signal_count=len(tech.get("signals", []) or []),
        )

        results = factor_analysis([row])
        if not results:
            return {"text": f"# {symbol} 4 因子打分\n\n数据不足，无法计算因子。"}

        item = results[0]
        text = f"""# {symbol} 4 因子打分

## 因子得分总览

| 因子 | 得分 | 说明 |
|---|---|---|
"""
        for f in item.get("factors", []):
            score = f.get("score")
            score_text = "数据不足" if score is None else f"{score:.1f} / 10"
            text += f"| {f.get('name', '')} | {score_text} | {f.get('comment', '')} |\n"

        total = item.get("total_score")
        total_text = "数据不足" if total is None else f"{total:.1f} / 10"
        text += f"""
## 综合评分

**综合得分：{total_text}**

"""
        if item.get("missing"):
            text += f"> ⚠️ 缺失维度（未计入综合分）：{'、'.join(item['missing'])}\n\n"
        text += "> ⚠️ 因子打分仅供参考，不构成投资建议。\n"
        return {"text": text}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/industry")
def api_industry(name: str):
    """行业分析 - 接真实数据。"""
    from src.services.research_assistant import ResearchAssistant

    settings = get_settings()
    ra = ResearchAssistant(settings)
    try:
        result = ra.summarize_industry(name)
        return {"text": result.text}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/report", response_model=ReportResponse)
def api_report(topic: str, deep: bool = False):
    settings = get_settings()
    orchestrator = ResearchOrchestrator(settings)
    try:
        report = orchestrator.run(topic, deep=deep)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    files = export_report(report, settings.output_dir, ("md", "html"))
    parts = [f"# {report.topic}", "", report.abstract, ""]
    for s in report.sections:
        parts.append(f"## {s.heading}\n{s.content}\n")
    return ReportResponse(
        text="\n".join(parts),
        sections=len(report.sections),
        references=len(report.references),
        output_file=str(files[0]) if files else "",
    )


# 3.0：SSE 流式进度推送接口
@app.get("/api/report/stream")
async def api_report_stream(
    topic: str, deep: bool = False, provider: Optional[str] = None
):
    """Server-Sent Events 实时推送研报生成进度，完成后返回全文。

    旧实现把各步骤名先攒进列表、等整条流水线跑完才一次性吐给前端，
    所以"实时进度"是假的（spinner 全程停在第一步）。现在改为：
    工作线程每完成一步就通过线程安全队列立刻推给前端。
    """
    base_settings = get_settings()
    if provider and provider != "auto":
        # 用户在「深度研报」页选择的搜索引擎
        base_settings.search_provider = provider

    async def generate():
        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[str]" = asyncio.Queue()

        def on_step(name: str, ctx):
            # 从工作线程投递到事件循环
            loop.call_soon_threadsafe(queue.put_nowait, name)

        orchestrator = ResearchOrchestrator(base_settings)
        future = loop.run_in_executor(
            None, lambda: orchestrator.run(topic, on_step=on_step, deep=deep)
        )

        def _drain() -> List[str]:
            out = []
            while not queue.empty():
                out.append(queue.get_nowait())
            return out

        try:
            while True:
                for name in _drain():
                    payload = json.dumps({"type": "step", "name": name}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                if future.done():
                    break
                await asyncio.sleep(0.1)
            for name in _drain():
                payload = json.dumps({"type": "step", "name": name}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            report = await future
        except Exception as exc:  # noqa: BLE001
            payload = json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            return

        try:
            export_report(report, base_settings.output_dir, ("md", "html"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("研报导出失败（不影响返回文本）：%s", exc)

        parts = [f"# {report.topic}", "", report.abstract, ""]
        for s in report.sections:
            parts.append(f"## {s.heading}\n{s.content}\n")
        meta = report.meta or {}
        if "elapsed_seconds" in meta:
            parts.append(
                f"\n---\n*生成耗时 {meta['elapsed_seconds']}s｜"
                f"章节 {len(report.sections)}｜参考资料 {len(report.references)}*"
            )

        payload = json.dumps(
            {"type": "done", "text": "\n".join(parts)}, ensure_ascii=False
        )
        yield f"data: {payload}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# 轻量实时行情：供「自动刷新」使用
#
# 旧实现每 10 秒调用一次 /api/brief —— 那会完整跑一遍 LLM 生成 + 语义校验 +
# 三表抓取 + 向量库写入 + 导出文件，挂着页面过夜等于成千上万次付费调用。
# 现在自动刷新只调用这个纯行情接口。
# ---------------------------------------------------------------------------
@app.get("/api/quote")
def api_quote(symbol: str):
    """返回单只股票的最新价与涨跌幅（不调用大模型、不写文件）。"""
    from src.data.sources import AKShareSource, DataSourceError

    source = AKShareSource()
    try:
        code = source.normalize_symbol(symbol)
    except Exception:  # noqa: BLE001
        code = symbol

    try:
        import akshare as ak
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"未安装 akshare：{exc}")

    # 优先用轻量的单只股票盘口接口；失败再退回全市场快照
    try:
        df = ak.stock_bid_ask_em(symbol=code)
        data = dict(zip(df["item"], df["value"]))
        price = float(data.get("最新"))
        pct = float(data.get("涨幅"))
        name = str(data.get("名称", "") or code)
        return {
            "symbol": code,
            "name": name,
            "price": round(price, 3),
            "pct_change": round(pct, 2),
            "source": "eastmoney-bidask",
        }
    except Exception as first_exc:  # noqa: BLE001
        logger.info("盘口接口失败，改用全市场快照：%s", first_exc)

    try:
        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"] == code]
        if row.empty:
            raise DataSourceError(f"未找到股票代码 {code} 的实时行情")
        return {
            "symbol": code,
            "name": str(row.iloc[0]["名称"]),
            "price": round(float(row.iloc[0]["最新价"]), 3),
            "pct_change": round(float(row.iloc[0]["涨跌幅"]), 2),
            "source": "eastmoney-spot",
        }
    except DataSourceError:
        raise HTTPException(status_code=404, detail=f"未找到股票代码 {code}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"实时行情获取失败：{exc}")


# ---------------------------------------------------------------------------
# Web「设置」页读写接口
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def api_get_settings():
    """读取已保存的推送/预警设置（密钥做脱敏）。"""
    stored = _load_web_settings()
    out: Dict[str, str] = {}
    for field in list(_WEB_SETTINGS_KEYS) + ["alert_up_pct", "alert_down_pct"]:
        value = stored.get(field, "")
        if field in _SECRET_FIELDS and value:
            value = ("*" * max(0, len(value) - 4)) + value[-4:]
        out[field] = value
    out["config_file"] = str(WEB_SETTINGS_PATH)
    return out


@app.post("/api/settings")
def api_save_settings(payload: Dict[str, str]):
    """保存推送渠道与预警阈值。"""
    try:
        values = {k: v for k, v in (payload or {}).items() if v is not None}
        # 脱敏后的密钥若被原样回传，不要覆盖真实值
        for field in _SECRET_FIELDS:
            if str(values.get(field, "")).startswith("*"):
                values.pop(field, None)
        _save_web_settings(values)
        return {"ok": True, "saved": len(values), "config_file": str(WEB_SETTINGS_PATH)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


# 3.0：MCP 端点（Model Context Protocol，JSON-RPC 2.0）
@app.post("/mcp")
def mcp_endpoint(request: dict):
    """JSON-RPC 2.0 over HTTP 的 MCP 端点（裸 POST，非 Streamable HTTP）。

    这是给脚本 / curl / 自己写的 HTTP 客户端用的 JSON-RPC 接口；
    Claude Desktop 等要求 Streamable HTTP（session + GET/SSE）的客户端请改用
    仓库根目录的 stdio 入口 ``python mcp_server.py``（见其顶部配置示例）。

    示例调用：
        curl -X POST http://localhost:8000/mcp \\
          -H "Content-Type: application/json" \\
          -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    """
    from src.mcp.server import MCPServer
    settings = get_settings()
    server = MCPServer(settings)
    return server.handle(request)


@app.get("/mcp/tools")
def mcp_tools_info():
    """浏览器直接查看 MCP 暴露的工具列表。"""
    from src.mcp.server import TOOLS_DECLARATION
    return {"mcp_version": "1.0", "tools": TOOLS_DECLARATION}


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
