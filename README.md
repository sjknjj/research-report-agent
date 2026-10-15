# 研报辅助 Agent 3.0 · Research Report Agent

基于 **大模型 + AKShare** 的轻量化 A 股投研助手：批量抓取 A 股上市公司**公开行情与财报数据并完成清洗**，
调用**大模型 API**自动汇总行业信息、提炼公告要点，并通过**确定性的数字交叉校验**约束模型输出，
系统性规避 AI「数字幻觉」。同时内置一套通用多智能体网页研报流水线。

> **完整使用教程见 [USAGE.md](./USAGE.md)**：从环境安装、密钥配置到六大命令、Python 库调用、常见问题。

---

## 🚀 v3.4 功能大升级（对标优秀开源项目 + 反幻觉做到极致）

### ✨ 新增功能
| 功能 | 说明 |
| --- | --- |
| **多LLM提供商一键切换** | 支持 OpenAI / DeepSeek / 通义千问 / Kimi / 智谱 / 本地 Ollama |
| **更多推送渠道** | 新增 Telegram 推送（飞书/企微/Telegram 三选一或全用） |
| **YAML 策略配置** | `config.example.yaml` 自定义分析维度、校验规则、辩论模式 |
| **三方辩论模式** | 从多空两方升级为 技术面/基本面/情绪面 三方辩论 |
| **K线形态识别** | 自动识别十字星/大阳大阴/双底(W)/双顶(M) 等常见形态 |
| **反幻觉可信度等级** | 每份报告自动打 A/B/C/D 可信度评分，一眼看出靠不靠谱 |

### 🛡️ 反幻觉核心优势（别的项目没有）
- 数字交叉校验：生成→比对真实数据→自动纠错
- 可信度等级：A（<5%幻觉）/ B（5-15%）/ C（15-30%）/ D（>30%）
- 严格模式：发现矛盾直接拒绝输出
- 自动纠错：发现错误自动让模型重写，直到通过校验

---

## 🚀 v3.3 体验大升级（新功能 + 易用性提升）

### ⚡ 性能提升
| 新功能 | 说明 | 效果 |
| --- | --- | --- |
| **本地数据缓存** | 同一天同一只股票的数据自动缓存到 `cache/` 目录 | 第二次生成速度从 30 秒 → 2 秒，LLM 成本也降了 |

### 🖥️ 新增命令
| 命令 | 功能 | 示例 |
| --- | --- | --- |
| `python main.py history` | 查看所有历史生成的报告 | `python main.py history --keyword 600519` |
| `python main.py monitor` | 盘中实时监控自选股涨跌，达阈值自动推送 | `python main.py monitor --threshold 5 --interval 60` |

### 💬 易用性提升
| 优化项 | 说明 |
| --- | --- |
| **友好错误提示** | 缺 API Key / 网络超时 / 代码格式错 等常见错误，直接告诉你怎么修 |
| **CLI 彩色输出** | 标题、校验结果、成功/失败都用颜色区分，终端更清晰 |
| **配置示例文件** | 新增 `config.example.yaml`，所有配置一目了然 |

---

## 🔧 v3.2 架构重构（代码瘦身 + 可维护性提升）

本次重构围绕**消除重复代码、统一公共能力**展开，代码总行数减少约 300 行，同时保持所有功能完全不变：

| 重构项 | 说明 | 效果 |
| --- | --- | --- |
| **BaseAgent 统一 LLM 调用** | 在基类新增 `_safe_chat_json()` / `_safe_chat()` 两个方法，自动 try/except + 兜底数据 | 5 个 agent 各自手写的 ~80 行重复 try/except 全部删除，统一兜底逻辑 |
| **配置单例缓存** | 新增 `get_settings()` 模块级单例，Web / MCP 常驻进程只加载一次 .env | 避免每个 API 端点重复读盘，响应更快 |
| **推送能力下沉** | `push_brief_result()` 公共函数下沉到 `src/notify/` 公共层 | CLI / Web / MCP 三个入口共用一套推送逻辑，不用各自重复实现 |
| **死代码清理** | 删除 4 个 agent 中不再使用的 `LLMError` import | 代码更干净 |

> 本次重构为**纯内部优化**，所有对外接口、CLI 命令、Web API、MCP 工具签名完全不变，已有代码无需任何改动即可升级。

---

## 🆕 v3.0 版本更新内容

本次升级围绕**性能、体验、反幻觉深度**三个方向做了核心改造：

### ⚡ 性能提升
| 优化项 | 说明 | 效果 |
| --- | --- | --- |
| **Analyst 章节并行分析** | `src/agent/analyst.py` 改为线程池并行（默认4线程），各章节独立调用LLM | 7章报告的分析阶段从串行→并行，提速约 60% |
| **Writer 章节并行撰写** | `src/agent/writer.py` 各章节正文并行生成，按原始顺序组装 | 撰写阶段同步提速，整体生成等待时间大幅缩短 |

### 🖥️ Web 体验升级
| 优化项 | 说明 |
| --- | --- |
| **SSE 实时进度推送** | `/api/report/stream` 接口，通过 Server-Sent Events 实时推送流水线各阶段进度（规划→检索→分析→撰写→审校） |
| **前端进度条** | 浏览器端实时显示当前执行到哪一步，不再是干等转圈 |
| **版本标识** | 页面标题显示 v3.0，新特性提示 |

### 🛡️ 反幻觉能力升级
| 优化项 | 说明 |
| --- | --- |
| **引用覆盖率校验** | `src/agent/reviewer.py` 新增机械校验：统计正文中带 `[n]` 引用标注的句子比例，低于30%自动报警 |
| **审校日志增强** | 日志输出引用覆盖率百分比，便于评估报告质量 |
| **引用覆盖率纳入审校结论** | 覆盖率过低时作为问题项写入审校结果，触发writer修订 |

### ⚔️ 多空辩论机制（新增）
| 优化项 | 说明 |
| --- | --- |
| **Bull vs Bear 对抗分析** | `src/agent/debate.py` 新增辩论智能体：多方专找增长逻辑、空方专找风险，双方各3-5个论点 |
| **两轮互相反驳** | 双方针对对方最强论点做一轮反驳，避免单边乐观视角 |
| **多空对照表** | 报告末尾自动生成「乐观 vs 谨慎」对照表 + 综合平衡判断 |
| **多空评分** | 0-10分量化多空力量对比（5为中性），写入报告meta |

### 📊 决策仪表盘（新增）
| 优化项 | 说明 |
| --- | --- |
| **速览卡片置顶** | 报告最开头自动生成「速览仪表盘」：评分、综合判断、核心驱动、主要风险，一眼抓重点 |
| **评分文字标签** | 8分以上"强烈偏多"、6分以上"偏多"、4-6"中性"、2-4"偏空"、2以下"强烈偏空" |

### 📬 消息推送（新增）
| 优化项 | 说明 |
| --- | --- |
| **飞书机器人推送** | `src/notify/feishu.py`：自定义机器人 webhook，支持签名校验 |
| **企业微信推送** | `src/notify/wecom.py`：群机器人 webhook |
| **CLI --push 参数** | `python main.py brief 600519 --push` 生成完自动推送到配置的群 |
| **配置方式** | `.env` 里填 `FEISHU_WEBHOOK_URL` / `WECOM_WEBHOOK_KEY` 即可启用 |

### 🔌 MCP Server 接口（新增）
| 优化项 | 说明 |
| --- | --- |
| **MCP 协议支持** | `src/mcp/server.py`：遵循 Model Context Protocol（JSON-RPC 2.0） |
| **暴露3个工具** | `get_stock_brief`（公司速览）、`verify_numbers`（数字反幻觉校验）、`generate_stock_report`（完整研报） |
| **HTTP 端点** | `POST /mcp` 可被 Claude Desktop / Cursor 直接接入 |
| **工具列表查询** | `GET /mcp/tools` 浏览器直接查看暴露的所有工具 |

> **兼容性**：全部 21 项单元测试通过，CLI 命令、离线demo、原有API均无破坏性变更。

---

## 🔗 同类开源项目参考（可吸收方向）

本项目在调研同类开源产品后，吸收了以下项目的设计思路，未来可进一步借鉴：

| 项目 | Star量级 | 可吸收的点 | 链接 |
| --- | --- | --- | --- |
| **TradingAgents-CN** | ~28K | 多空辩论机制（bull vs bear analyst 对抗后再决策）、三方风险审议流程 | [GitHub](https://github.com/hy172189325/TradingAgents-CN) |
| **daily_stock_analysis** | ~56K | 每日定时跑批 + 结果推送到企业微信/飞书/Telegram 的通知能力 | [GitHub](https://github.com/ZhuLinsen/daily_stock_analysis) |
| **FinRobot (AI4Finance)** | 学术框架 | 估值模型自动计算（DCF/可比公司）、财报情绪分析模块 | [GitHub](https://github.com/AI4FinanceFoundation/FinRobot) |
| **观澜 Financial Analyst** | PyPI | 24个专职agent分工（基本面/技术面/资金面/量化评分），比当前5agent粒度更细 | [PyPI](https://pypi.org/project/financial-analyst/) |
| **awesome-trading-agents** | 资源列表 | 持续追踪金融agent生态，含MCP servers、skills清单 | [GitHub](https://github.com/LLMQuant/awesome-trading-agents) |

> **当前项目差异化定位**：轻量化（纯Python无重型依赖）、反幻觉双层校验（数值+语义）、离线可跑。上述项目多为全栈重型方案（MongoDB/Redis/Docker/Vue），本项目刻意保持单文件可运行。

---

## 🔌 MCP（Model Context Protocol）支持情况

**当前版本：暂不支持 MCP 接口。**

| 维度 | 状态 | 说明 |
| --- | --- | --- |
| MCP Server 暴露工具 | ❌ | 项目当前是独立应用，未将 AKShare 数据接口、检索工具、校验器封装为 MCP Server |
| MCP Client 调用外部工具 | ❌ | researcher 的 search_tool 是内置实现（DuckDuckGo/Tavily），未走 MCP 协议对接外部工具 |
| 与 Claude Desktop / Cursor 集成 | ❌ | 无法作为 MCP 工具被 Claude / Cursor 直接调用 |

**未来规划**：
- v3.1 计划将 `src/data/sources.py` 的 AKShare 接口封装为 MCP Server，暴露 `get_stock_price` / `get_financials` / `get_news` 等工具
- v3.2 计划将 `CrossVerifier` 也作为 MCP 工具，供外部 agent 调用做数字反幻觉校验
- 到时候可直接在 Claude Desktop / Cursor 的 `mcp.json` 里接入本项目，把"A股数据+校验"作为工具使用

> MCP 是 Anthropic 推出的开放协议（类似AI界的USB-C），用于标准化LLM与外部工具/数据源的连接。本项目核心能力（A股数据+反幻觉校验）非常适合封装为MCP Server对外复用。

---

## 它能做什么

| 需求 | 实现 |
| --- | --- |
| **① Python + AKShare 抓 A 股数据并清洗** | `src/data`：10+ 接口（行情/财务/资金流/估值/龙虎榜/北向资金/解禁），自动重试 + 中文列名标准化 + 「亿/万/%」脏数据解析 |
| **② 大模型投研助手 + 双层反幻觉** | `src/services` + `src/verify`：数值层交叉校验（逐条数字核对）+ 语义层 Faithfulness 校验（无来源定性判断），自动纠错重写 |
| **③ 多智能体研报流水线** | `src/agent`：规划→递归深挖检索→分析→撰写→审校五阶段协作，支持多轮补检知识缺口 |
| **④ 技术面量化分析** | `src/analysis/indicators.py`：MACD/RSI/KDJ/布林带自动计算，金叉死叉、超买超卖、多头/空头排列信号生成 |
| **⑤ 产业链知识图谱** | `src/analysis/chain.py`：内置 14 条主流产业链（锂电池/半导体/光伏/医药/银行…），输入公司自动定位上下游并横向对比 |
| **⑥ RAG 历史报告检索** | `src/rag/vector_store.py`：本地向量库 + 余弦相似度，生成报告前检索历史相似案例；支持真实 embedding API（OpenAI 兼容），未配置自动回退本地哈希向量 |
| **⑦ 三大报表勾稽校验** | `src/verify/statements.py`：资产=负债+权益、净利润=利润总额−所得税、现金流期末勾稽、经营现金流/净利润利润质量等确定性核对 |
| **⑧ 多公司批量对比** | `src/analysis/comparison.py` + `compare` 命令：多家公司 PE/PB/增速/ROE/技术面横向对比，按任意维度排序 |
| **⑨ 报告导出 PDF** | `src/export/pdf_export.py`：reportlab 中文排版，标题/表格/列表多页导出，`--pdf` 一键开启 |
| **⑩ 可视化图表** | Plotly 交互式 K线/财务趋势/PE-PB 仪表盘（`charts.py`）+ networkx 产业链关系网络图（`chain_viz.py`），均嵌入 HTML 报告 |
| **⑪ Web 服务** | `web/app.py`：FastAPI 提供公司速览 + 深度研报生成 API，自带简易前端页面 |
| **⑫ CLI + 离线样例 + 单元测试** | `main.py`：brief/announce/industry/compare/report/serve/demo 七个子命令；`tests/` 21 项测试零回归 |

## 反幻觉是怎么做到的

大模型最危险的错误是「一本正经地编造财务数字」。本项目**不依赖模型自觉**，而是用确定性校验兜底：

```
AKShare 真实数据 → 清洗 → 事实库 FactBase（唯一数字来源，输入侧锁定）
                          │
                          ▼
              大模型只能依据「事实包」生成草稿
                          │
                          ▼
   CrossVerifier 抽取草稿中的每个数字声明，逐条与事实库按容差比对
                          │
        ┌─────────────────┴─────────────────┐
        ▼                                   ▼
   一致 supported                    矛盾 mismatch（疑似幻觉）
        │                                   │
        ▼                                   ▼
     输出成稿            回填「正确值纠错清单」→ 模型重写 → 再校验
```

实际效果（`examples/demo_cross_verification.py`）：模型把营收写成 182.30 亿、净利增速 15.20%、
毛利率 50%，校验器精确抓出三处矛盾并给出真实值 152.30 亿 / 9.40% / 42.30%：

```
交叉校验结论：未通过｜共核对 6 处数值：一致 3、矛盾 3、无法核实 0｜疑似幻觉率 50.0%
  [✗] 营业总收入：文本称 182.30 亿元，但经数据核算应为 152.30 亿元（2025-12-31）
  [✗] 归母净利润同比增速：文本称 15.20%，应为 9.40%
  [✗] 销售毛利率：文本称 50.00%，应为 42.30%
```

## 技术面 + 产业链 + RAG 三大分析能力

**① 技术面量化信号**（`src/analysis/indicators.py`）

基于清洗后的日线数据，自动计算四大常用技术指标并输出可解读的信号：

| 指标 | 识别的信号 |
| --- | --- |
| MACD | 金叉（DIF 上穿 DEA，偏多）、死叉（偏空）、红绿柱 |
| RSI(14) | >70 超买、<30 超卖 |
| KDJ | 金叉/死叉、J 值 >100 严重超买 / <0 严重超卖 |
| 布林带 | 突破上轨（强势乖离）、跌破下轨（弱势） |

同时结合 MA5/MA20/MA60 判断均线多头/空头排列，结果写入公司速览数据包。

**② 产业链知识图谱**（`src/analysis/chain.py`）

内置 14 条 A 股主流产业链的上下游关系，每条按「上游原材料/核心部件 → 中游制造/品牌 → 下游应用/渠道」三层组织，列出代表公司代码与业务定位。输入公司代码即自动匹配它所在产业链及位置，并在报告中附上全景对比：

```
锂电池、新能源车、白酒、半导体、光伏、风电、
医药、军工、银行、券商、消费电子、食品饮料、房地产、煤炭钢铁
```

**③ RAG 历史报告检索**（`src/rag/vector_store.py`）

不依赖外部向量数据库，纯 numpy 实现的本地向量库（n-gram 哈希向量化 + 余弦相似度）：

```
生成新报告 → 先检索历史报告库中同行业/同主题的相关片段
          → 作为参考背景注入 prompt（事实数字仍由事实包锁定，不引入幻觉）
          → 本次成稿自动入库，供后续报告检索
```

## 一、安装

```bash
cd research-report-agent
python3 -m venv .venv && source .venv/bin/activate   # 可选
pip install -r requirements.txt                      # akshare / pandas / numpy

cp .env.example .env                                 # 配置大模型密钥
```

模型走 **OpenAI 兼容协议**，在 `.env` 改两行即可切换厂商：

| 厂商 | `LLM_BASE_URL` | `LLM_MODEL` 示例 |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 火山·豆包方舟 | `https://ark.cn-beijing.volces.com/api/v3` | 接入点 `ep-xxxx` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Kimi / 智谱 | 见 `.env.example` 注释 | `moonshot-v1-32k` / `glm-4-flash` |

> 没有密钥也能体验：所有命令加 `--sample`（或 `--mode offline`）即用**离线虚构样例**跑通全流程。

## 二、运行流程（CLI）

```bash
# 0) 一键离线案例：公司速览 + 反幻觉抓错演示 + 公告校验（无需密钥/网络）
python main.py demo

# 1) 公司投研速览：抓 600519 近一年行情+多期财报+资讯，大模型生成并交叉校验
python main.py brief 600519
python main.py brief 600519 --start 20250101 --end 20251231   # 指定区间
python main.py brief 000000 --sample                          # 离线样例

# 2) 公告要点提炼：喂公告文本，结构化提炼并与财报交叉校验
python main.py announce --file 公告.txt --title 2025年年度业绩快报 --symbol 600519
python main.py announce --sample                              # 用内置样例公告

# 3) 行业信息汇总（板块成分聚合 + 大模型概述）
python main.py industry 白酒

# 4) 通用多智能体网页研报（规划→递归深挖检索→分析→撰写→审校）
python main.py report 低空经济产业链分析
python main.py report 低空经济产业链分析 --deep          # 开启递归深挖（多轮补检知识缺口）

# 5) 启动 Web 服务（FastAPI，自带前端页面）
python main.py serve                                      # 默认 http://127.0.0.1:8000

# 6) 历史报告检索
python main.py history                                    # 列出所有历史报告
python main.py history --keyword 600519                   # 按关键词搜索

# 7) 盘中实时监控（达涨跌幅阈值自动推送）
python main.py monitor                                    # 默认±5%，60秒轮询
python main.py monitor --threshold 3 --interval 30        # ±3%，30秒一次

# 通用参数：--mode {auto,online,offline}  --model X  --output DIR  --deep
```

产物默认输出到 `output/`，同时生成 Markdown 与自包含 HTML（含交叉校验表与事实包附录）。

---

## 🖥️ 方式一：Web 界面使用（最直观，推荐新手）

```bash
python main.py serve
```

浏览器打开 http://localhost:8000 ，你会看到三个 Tab：

| Tab | 功能 | 说明 |
|---|---|---|
| **📊 公司速览** | 输入股票代码 → 一键生成速览 | 含行情、财务、技术面、交叉校验反幻觉 |
| **📝 深度研报** | 输入研究主题 → 多智能体流水线生成 | SSE 实时显示进度（规划→检索→分析→辩论→撰写→审校） |
| **⭐ 我的自选股** | 浏览器本地存储自选股列表 | 点击股票代码快速生成速览，不用每次输入代码 |

**特点**：零命令操作、实时进度条、自选股本地保存，适合日常快速看盘。

---

## 📬 方式二：每日自选股自动推送（最省心，推荐进阶用户）

配一次之后，**每天自动收到飞书/企业微信通知**，不用打开任何页面。

### 第一步：配置推送渠道

在飞书/企业微信群里添加自定义机器人，拿到 webhook 地址，填到 `.env`：

```bash
# 飞书自定义机器人
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx
FEISHU_SECRET=你的签名密钥（可选）

# 企业微信机器人（二选一）
WECOM_WEBHOOK_KEY=你的webhook key
```

### 第二步：编辑自选股列表

打开 `watchlist.txt`，一行一个股票代码：

```
600519
000858
300750
601318
```

### 第三步：手动跑一次测试

```bash
python main.py daily
```

每只股票会自动生成速览并推送到群里。

### 第四步：GitHub Actions 定时自动跑（推荐）

1. 把代码推到你的 GitHub 仓库
2. 在仓库 Settings → Secrets 里添加：
   - `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`
   - `FEISHU_WEBHOOK_URL`、`FEISHU_SECRET`（或 WECOM_WEBHOOK_KEY）
3. 已经配好的 `.github/workflows/daily-report.yml` 会在**每个工作日早上 9 点（北京时间）**自动运行
4. 也可以在 Actions 页面手动触发运行

**效果**：每天早上打开微信，直接收到自选股分析卡片，零操作。

---

## 🔌 方式三：MCP 接入 Claude Desktop / Cursor（最极客）

本项目暴露了标准 MCP（Model Context Protocol）接口，你可以在 Claude Desktop / Cursor 里直接用自然语言调用：

> "帮我分析一下茅台现在怎么样"
> "校验一下这段文字里的财务数字对不对"

### 暴露的 3 个 MCP 工具

| 工具名 | 功能 | 输入 |
|---|---|---|
| `get_stock_brief` | 获取 A 股公司投研速览 | 股票代码（如 600519） |
| `verify_numbers` | 数字反幻觉校验 | 待校验文本 + 股票代码 |
| `generate_stock_report` | 生成完整深度研报 | 研究主题 |

### 接入 Claude Desktop 步骤

1. 先启动本项目的 Web 服务：`python main.py serve`
2. 找到 Claude Desktop 的配置文件：
   - Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
3. 加入以下配置：

```json
{
  "mcpServers": {
    "research-report-agent": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

4. 重启 Claude Desktop，就可以直接说"帮我分析下600519"了

### 接入 Cursor 步骤

1. 同样先启动 Web 服务
2. Cursor 设置 → Features → MCP → Add Server
3. URL 填 `http://localhost:8000/mcp`
4. 保存后即可在 Cursor 对话中调用

> **说明**：MCP 接口基于 HTTP + JSON-RPC 2.0，兼容大多数支持 MCP 的客户端。

---

## 三、案例演示（`examples/`）

| 脚本 | 演示内容 |
| --- | --- |
| `demo_a_share.py` | 公司速览全流程：数据清洗→事实锁定→生成→交叉校验→导出 |
| `demo_cross_verification.py` | **反幻觉核心**：故意写错 3 个数字，看校验器如何抓出并纠错 |
| `demo_announcement.py` | 公告要点提炼，并与财报数字交叉校验 |
| `demo_offline.py` | 通用网页研报的离线流程 |

运行任一案例：`python examples/demo_cross_verification.py`。
离线样例数据由 `scripts/generate_samples.py` 可复现生成（虚构数据，不对应真实证券），
存于 `data/samples/`，用于在无网络/未装 akshare 时演示「脏数据→清洗」。

## 四、作为 Python 库调用

```python
from src.common.config import load_settings
from src.services.research_assistant import ResearchAssistant
from src.export.exporter import export_assistant

settings = load_settings()
assistant = ResearchAssistant(settings)

# 公司速览（内部完成：AKShare 抓取→清洗→事实包→大模型→交叉校验→必要时重写）
result = assistant.company_brief("600519")
print(result.text)
print(result.verification)          # supported / mismatch / unverifiable 明细
export_assistant("贵州茅台 投研速览", result.text, verification=result.verification,
                 fact_pack=result.fact_pack)

# 公告要点
result = assistant.extract_announcement(open("公告.txt", encoding="utf-8").read(),
                                        title="年报", symbol="600519")
```

## 五、工程结构

```
research-report-agent/
├── main.py                       # CLI：brief/announce/industry/compare/report/serve/demo
├── config.example.json .env.example requirements.txt
├── src/
│   ├── data/                     # ① 数据层（10+ AKShare 接口）
│   │   ├── sources.py            #   行情/财务/资金流/估值/龙虎榜/北向/解禁
│   │   ├── cleaner.py            #   中文列名/亿万百分号/缺失值清洗 + 派生指标
│   │   ├── data_service.py       #   实时抓取→清洗→失败回退样例（CompanyBundle）
│   │   └── sample_data.py        #   离线样例加载
│   ├── verify/                   # 反幻觉校验 + 三表勾稽
│   │   ├── facts.py              #   事实库 FactBase + 事实包渲染（输入锁定）
│   │   ├── verifier.py           #   数值声明抽取 + 逐条核对 + 纠错清单
│   │   ├── semantic_check.py     #   语义层 Faithfulness 校验（无来源判断识别）
│   │   └── statements.py         #   三大报表勾稽（恒等式/利润质量）
│   ├── services/
│   │   └── research_assistant.py #   投研助手编排 + 语义校验纠错闭环
│   ├── llm/client.py             # OpenAI 兼容客户端 + 离线兜底
│   ├── agent/                    # 多智能体流水线（规划/检索/分析/撰写/审校）
│   │   └── researcher.py         #   递归深挖检索（多轮补检知识缺口）
│   ├── analysis/                 # 技术面 + 产业链 + 多公司对比
│   │   ├── indicators.py         #   MACD/RSI/KDJ/布林带信号
│   │   ├── chain.py              #   14 条产业链上下游图谱
│   │   ├── chain_viz.py          #   networkx 产业链关系图
│   │   └── comparison.py         #   多公司横向对比与排序
│   ├── rag/                      # RAG 向量检索
│   │   ├── vector_store.py       #   本地向量库（余弦相似度）
│   │   └── embedding.py          #   真实 embedding API + 本地哈希回退
│   ├── tools/                    # 网页检索 / 抓取
│   ├── common/                   # 配置 / 数据结构 / 日志
│   └── export/
│       ├── exporter.py           # Markdown / HTML / PDF 导出
│       ├── charts.py             # Plotly K线图/财务趋势图/PE-PB 仪表盘
│       └── pdf_export.py         # reportlab 中文 PDF 排版
├── web/
│   └── app.py                    # FastAPI Web 服务 + 前端页面
├── data/samples/                 # 离线样例「原始脏数据」CSV
├── examples/                     # 案例脚本
├── USAGE.md                      # 完整使用教程
├── scripts/generate_samples.py   # 样例数据可复现生成
└── tests/                        # 21 项单元 + 端到端测试
```

## 六、数据来源（AKShare 接口）

| 数据 | 接口 | 说明 |
| --- | --- | --- |
| 日线行情（前复权） | `stock_zh_a_hist` | 中文列名标准化、均线/波动率/区间涨跌 |
| 财务摘要（多期） | `stock_financial_abstract_ths` | 「1.47亿/46.84%/False」→ 数值与缺失值 |
| 个股信息 | `stock_individual_info_em` | 长表转字典，失败不阻塞主流程 |
| 公司资讯 | `stock_news_em` | 标题/正文/时间/来源标准化 |
| 行业成分 | `stock_board_industry_cons_em` | 板块涨跌聚合，限流时回退 |
| **资金流向** | `stock_individual_fund_flow` | 主力/超大单/大单净流入 |
| **估值指标** | `stock_a_indicator_lg` | PE / PB / PS / 股息率 |
| **龙虎榜** | `stock_lhb_detail_em` | 上榜日期、买卖席位、净额 |
| **北向资金** | `stock_hsgt_individual_em` | 个股北向持股数量与占比 |
| **解禁数据** | `stock_restricted_release_queue_em` | 解禁日期、解禁数量、占总股本比 |

> 公开数据接口存在限流/偶发断连，本项目对每个接口做有限次重试，
> 关键数据失败时自动回退到离线样例并显著标注，保证流程不中断、不把样例冒充真实数据。

## 七、运行测试

```bash
python3 -m unittest discover -s tests -v     # 21 项：清洗 / 反幻觉 / 助手 / 流水线
```

## 八、路线图

- [x] 接入估值（PE/PB）、北向资金、主力资金流、龙虎榜、解禁等 AKShare 接口
- [x] 财务图表自动可视化（Plotly K线/趋势图/PE-PB 仪表盘）
- [x] 语义层 Faithfulness 校验（识别无来源定性判断）
- [x] 递归深挖检索（多轮补检知识缺口）
- [x] FastAPI 服务化与 Web 界面
- [x] 技术面指标（MACD/RSI/KDJ/布林带）与信号生成
- [x] 产业链知识图谱（14 条主流产业链）+ networkx 关系图可视化
- [x] RAG 本地向量检索（历史报告复用）+ 真实 embedding API 接入
- [x] 三大报表勾稽校验（资产负债表/利润表/现金流量表）
- [x] 多公司批量对比与行业内排序
- [x] 报告导出 PDF（中文排版）
- [ ] 自动抓取三大报表原始科目（AKShare 资产负债表接口）并接入勾稽校验
- [ ] 产业链图谱交互式探索（点击节点展开关联公司）
- [ ] 多公司对比雷达图 / 估值分位图

## 免责声明

本项目仅用于技术研究与学习。AKShare 数据与 AI 生成内容可能存在错误、延迟或偏差，
**不构成任何投资建议**；请以交易所及上市公司正式披露文件为准并自行核验。

## License

[GNU AGPL v3.0](LICENSE)

> 本项目采用 GNU Affero General Public License v3.0 开源协议。
> 如果你在网络服务器上修改并使用本项目，需向所有用户公开修改后的完整源代码。
