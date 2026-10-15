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
</style></head><body>
<h1>研报辅助 Agent <span class="version">v3.0</span></h1>
<div class="subtitle">大模型 + AKShare · 数字交叉校验反幻觉 · 多空辩论 · 决策仪表盘</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('brief')">📊 公司速览</div>
  <div class="tab" onclick="switchTab('report')">📝 深度研报</div>
  <div class="tab" onclick="switchTab('watchlist')">⭐ 我的自选股</div>
</div>

<!-- 公司速览 Tab -->
<div id="tab-brief" class="tab-content active">
  <div class="card">
    <h3>公司投研速览</h3>
    <div class="row">
      <input id="symbol" placeholder="A 股代码，如 600519" value="600519">
      <button onclick="startBrief()">生成速览</button>
      <button class="secondary" onclick="addToWatchlist()">+ 加入自选</button>
    </div>
    <div class="tip">行情 + 财务 + 资讯 + 技术面 + 数字交叉校验（反幻觉）</div>
    <div id="brief_result" class="result" style="display:none;"></div>
  </div>
</div>

<!-- 深度研报 Tab -->
<div id="tab-report" class="tab-content">
  <div class="card">
    <h3>深度研报生成（多智能体流水线）</h3>
    <div class="row">
      <input id="topic" placeholder="研究主题，如 低空经济产业链分析">
      <button onclick="startReport()">生成研报</button>
    </div>
    <div class="row">
      <label class="tip" style="margin:0;"><input type="checkbox" id="deep"> 递归深挖检索（更慢但更全）</label>
    </div>
    <div class="tip">流程：规划 → 检索 → 逐章分析 → 多空辩论 → 撰写 → 审校（并行生成）</div>
    <div id="report_progress" style="margin-top:16px;font-size:13px;color:#6b7280;"></div>
    <div id="report_result" class="result" style="display:none;"></div>
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

<script>
function switchTab(name){
  document.querySelectorAll('.tab').forEach((t,i)=>{
    t.classList.toggle('active', ['brief','report','watchlist'][i]===name);
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
  const el=document.getElementById('brief_result');
  el.style.display='block';
  el.innerHTML='<span class="spinner"></span>抓取数据 + 生成分析 + 交叉校验中…';
  try{
    const r=await fetch('/api/brief?symbol='+encodeURIComponent(sym));
    const d=await r.json();
    if(d.error){el.textContent='错误：'+d.error;return;}
    el.textContent=d.text;
    if(d.verification && d.verification.passed !== undefined){
      const v=d.verification;
      el.textContent += `\\n\\n---\\n交叉校验：${v.passed?'通过':'未通过'}｜一致${v.supported}、矛盾${v.mismatch}、幻觉率${(v.hallucination_rate*100).toFixed(1)}%`;
    }
  }catch(e){el.textContent='请求失败：'+e;}
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
      resultEl.textContent=data.text;
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
def api_brief(symbol: str):
    settings = get_settings()
    ra = ResearchAssistant(settings)
    try:
        result = ra.company_brief(symbol, prefer_live=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
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
