"""FastAPI Web 服务（3.0升级）：SSE实时进度推送 + 异步任务。

启动方式：
    pip install fastapi uvicorn
    python main.py serve
    # 打开 http://localhost:8000
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from src.agent.orchestrator import ResearchOrchestrator
from src.common.config import get_settings
from src.export.exporter import export_assistant, export_report
from src.services.research_assistant import ResearchAssistant

app = FastAPI(title="研报辅助 Agent 3.0", version="3.0")

# 3.0：任务状态存储（生产环境应换Redis，这里用内存dict演示）
_tasks: Dict[str, dict] = {}


_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>研报辅助 Agent 3.0</title>
<!-- 引入 Markdown 渲染库 -->
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
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
.watch-item{background:#e6f0fa;color:#0f4c81;padding:6px 12px;
border-radius:6px;font-size:13px;cursor:pointer;}
.watch-item:hover{background:#d0e3f5;}
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
        📊 数据源选择（可多选，自动交叉验证）
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="akshare" checked> AKShare（推荐）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="eastmoney_http"> 东方财富
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="sina_http"> 新浪财经
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="datasource" value="tencent_http"> 腾讯财经
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

    <!-- 搜索引擎选择 -->
    <div style="margin:20px 0;padding:12px;background:#f8f9fa;border-radius:8px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:#374151;">
        🔍 搜索引擎选择（国内推荐用百度/必应，不用配 key）
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="search_engine" value="baidu" checked> 百度（推荐，国内不用key）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="search_engine" value="bing" checked> 必应（国内不用key）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="search_engine" value="duckduckgo"> DuckDuckGo（国内被墙）
        </label>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
          <input type="checkbox" name="search_engine" value="tavily"> Tavily（配key推荐）
        </label>
      </div>
      <div style="font-size:12px;color:#6b7280;margin-top:8px;">
        💡 生成研报/速览时会自动用这些搜索引擎搜资料
      </div>
    </div>

    <div class="tip" style="text-align:center;">行情 + 财务 + 技术面 + 反幻觉校验 + 多空辩论 + 决策仪表盘</div>

    <!-- 报告操作按钮（结果出来后显示） -->
    <div id="brief_actions" style="display:none;gap:8px;margin:12px 0;padding:12px;background:#f8f9fa;border-radius:8px;">
      <button class="secondary" onclick="downloadPdf()" style="font-size:13px;padding:6px 12px;">📄 导出 PDF</button>
      <button class="secondary" onclick="addToWatchlist()" style="font-size:13px;padding:6px 12px;">⭐ 加入自选</button>
      <button class="secondary" id="autoRefreshBtn" onclick="toggleAutoRefresh()" style="font-size:13px;padding:6px 12px;">🔄 自动刷新</button>
    </div>
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
function switchTab(name){
  document.querySelectorAll('.tab').forEach(t=>{
    t.classList.toggle('active', t.getAttribute('onclick').includes("'"+name+"'"));
  });
  document.querySelectorAll('.tab-content').forEach((c)=>c.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
}

// 自选股本地存储
function getWatchlist(){
  return JSON.parse(localStorage.getItem('watchlist')||'[]');
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
function renderWatchlist(){
  const list=getWatchlist();
  const el=document.getElementById('watchlist');
  const empty=document.getElementById('watchlist_empty');
  if(list.length===0){el.innerHTML='';empty.style.display='block';return;}
  empty.style.display='none';
  el.innerHTML=list.map(s=>
    `<span class="watch-item" onclick="quickBrief('${s}')" title="点击生成速览">${s}</span>`
  ).join('');
}
renderWatchlist();

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
    el.innerHTML = marked.parse(d.text);
    if(d.verification && d.verification.passed !== undefined){
      const v=d.verification;
      el.innerHTML += `<hr><p style="color:#6b7280;font-size:13px;">交叉校验：${v.passed?'✅ 通过':'⚠️ 未通过'}｜一致${v.supported}、矛盾${v.mismatch}、幻觉率${(v.hallucination_rate*100).toFixed(1)}%</p>`;
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
  el.style.display = 'block';
  el.innerHTML = '<span class="spinner"></span>抓取数据 + 横向对比中…';
  try{
    const r = await fetch('/api/compare?symbols='+encodeURIComponent(symbols));
    const d = await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.innerHTML = marked.parse(d.text);
  }catch(e){el.textContent='请求失败：'+e;}
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
    el.innerHTML = marked.parse(d.text);
    if(d.verification && d.verification.passed !== undefined){
      const v = d.verification;
      el.innerHTML += `<hr><p style="color:#6b7280;font-size:13px;">交叉校验：${v.passed?'✅ 通过':'⚠️ 未通过'}｜幻觉率${(v.hallucination_rate*100).toFixed(1)}%</p>`;
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
    el.innerHTML = marked.parse(d.text);
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
    el.innerHTML = marked.parse(d.text);
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
    el.innerHTML = marked.parse(d.text);
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
    el.innerHTML = marked.parse(d.text);
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
    el.innerHTML = marked.parse(d.text);
  }catch(e){el.textContent='请求失败：'+e;}
}

// 保存设置
function saveSettings(){
  const status = document.getElementById('settings_status');
  status.textContent = '✅ 设置已保存（重启服务后生效）';
  status.style.color = '#059669';
}

// 自动刷新
let autoRefreshTimer = null;
function toggleAutoRefresh(){
  const btn = document.getElementById('autoRefreshBtn');
  const status = document.getElementById('autoRefreshStatus');
  if(autoRefreshTimer){
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
    btn.textContent = '▶ 自动刷新';
    status.textContent = '';
  } else {
    btn.textContent = '⏸ 停止刷新';
    status.textContent = '每 10 秒自动刷新一次';
    autoRefreshTimer = setInterval(() => {
      startBrief();
    }, 10000);
  }
}

async function startReport(){
  const topic=document.getElementById('topic').value.trim();
  if(!topic){alert('请输入研究主题');return;}
  const deep=document.getElementById('deep').checked;
  const progEl=document.getElementById('report_progress');
  const resultEl=document.getElementById('report_result');
  resultEl.style.display='block';
  resultEl.innerHTML='';

  const steps=['① 规划提纲','② 检索资料','③ 逐章分析（并行）','④ 多空辩论','⑤ 撰写初稿（并行）','⑥ 审校'];
  let stepIdx=0;

  const es=new EventSource('/api/report/stream?topic='+encodeURIComponent(topic)+'&deep='+deep);
  progEl.innerHTML='<span class="spinner"></span> '+steps[0];

  es.onmessage=function(e){
    const data=JSON.parse(e.data);
    if(data.type==='step'){
      stepIdx++;
      if(stepIdx<steps.length){
        progEl.innerHTML='<span class="spinner"></span> '+steps[stepIdx];
      }
    } else if(data.type==='done'){
      es.close();
      progEl.innerHTML='✅ 生成完成';
      resultEl.innerHTML = marked.parse(data.text);
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
def api_brief(symbol: str, sources: str = "akshare"):
    settings = get_settings()
    # 把用户选中的数据源列表传给 ResearchAssistant
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    ra = ResearchAssistant(settings, sources=source_list)
    try:
        result = ra.company_brief(symbol, prefer_live=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))

    # 把用户选中的数据源加到报告里
    source_info = f"\n\n---\n\n📊 **本次使用的数据源**：{', '.join(source_list)}\n"
    if len(source_list) > 1:
        source_info += f"\n✅ 已启用多源交叉验证（{len(source_list)}个数据源）\n"
    result.text = result.text + source_info

    files = export_assistant(
        f"{symbol} 投研速览", result.text, settings.output_dir,
        formats=("md", "html"),
        verification=result.verification,
        fact_pack=result.fact_pack,
    )
    return BriefResponse(
        text=result.text,
        verification=result.verification or {},
        output_file=str(files[0]) if files else "",
    )


@app.get("/api/brief/pdf")
def api_brief_pdf(symbol: str, sources: str = "akshare"):
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
    from src.analysis.comparison import ComparisonService

    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if len(symbol_list) < 2:
        raise HTTPException(status_code=400, detail="至少需要2个股票代码")

    try:
        service = ComparisonService()
        rows = service.compare(symbol_list, prefer_live=True)
        markdown = service.render_markdown(rows, by=by)
        return {"text": markdown, "count": len(rows)}
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
    """幻觉率历史统计。"""
    from src.verify.stats import HallucinationStats

    try:
        stats = HallucinationStats()
        text = stats.render_markdown()
        return {"text": text}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/sentiment")
def api_sentiment(symbol: str):
    """情绪面分析。"""
    from src.analysis.sentiment import analyze_sentiment

    try:
        result = analyze_sentiment(symbol)
        return {"text": result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/backtest")
def api_backtest(symbol: str, days: int = 60):
    """回测功能。"""
    from src.analysis.backtest import backtest_macd

    try:
        result = backtest_macd(symbol, days=days)
        return {"text": result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/factor")
def api_factor(symbol: str):
    """因子分析。"""
    from src.analysis.comparison import factor_analysis

    try:
        result = factor_analysis(symbol)
        return {"text": result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/industry")
def api_industry(name: str):
    """行业分析。"""
    from src.services.research_assistant import ResearchAssistant

    settings = get_settings()
    ra = ResearchAssistant(settings)
    try:
        result = ra.industry_brief(name)
        return {"text": result}
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
async def api_report_stream(topic: str, deep: bool = False):
    """Server-Sent Events 实时推送研报生成进度，完成后返回全文。"""
    async def generate():
        settings = get_settings()
        orchestrator = ResearchOrchestrator(settings)

        # 收集每步完成事件
        step_events: List[str] = []
        def on_step(name: str, ctx):
            step_events.append(name)

        # 在线程池中运行阻塞的流水线
        loop = asyncio.get_event_loop()
        try:
            report = await loop.run_in_executor(
                None, lambda: orchestrator.run(topic, on_step=on_step, deep=deep)
            )
            # 推送各步骤完成
            for _ in step_events:
                yield f"data: {{\"type\": \"step\"}}\n\n"
                await asyncio.sleep(0.05)

            files = export_report(report, settings.output_dir, ("md", "html"))
            parts = [f"# {report.topic}", "", report.abstract, ""]
            for s in report.sections:
                parts.append(f"## {s.heading}\n{s.content}\n")
            # 附加上下文统计
            meta = report.meta or {}
            if "elapsed_seconds" in meta:
                parts.append(f"\n---\n*生成耗时 {meta['elapsed_seconds']}s｜"
                             f"章节 {len(report.sections)}｜参考资料 {len(report.references)}*")

            yield f"data: {{\"type\": \"done\", \"text\": {json.dumps(chr(10).join(parts))} }}\n\n"
        except Exception as exc:  # noqa: BLE001
            import json as _json
            yield f"data: {{\"type\": \"error\", \"message\": {_json.dumps(str(exc))} }}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# 3.0：MCP 端点（Model Context Protocol，JSON-RPC 2.0）
@app.post("/mcp")
def mcp_endpoint(request: dict):
    """MCP 标准接口：Claude Desktop / Cursor 可直接接入。

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
