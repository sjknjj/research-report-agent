# 投研辅助 Agent · Research Report Agent

**面向 A 股的投研报告生成 Agent，主打「可核实的反幻觉」而不是「看起来很像的报告」。**

基于大模型 + AKShare，自动生成投研报告；不依赖模型自觉，而是用**确定性校验**把
「数字对不对、有没有来源、引用站不站得住」逐条摊开给你看。

> 📖 零基础逐步教程：[USAGE.md](./USAGE.md)

---

## ⚠️ 先说实话：它能做什么，不能做什么

**没有任何系统能"确保报告不出错"。** 本项目的目标是 **"零未标注的不确定性"**——
把系统**核实不了**的部分显式暴露出来，而不是笼统地给一个"通过"。

**✅ 能做到**

- 报告里的**每个财务数字都被分类**：一致 / 矛盾 / 无法核实 / **无来源**；
- 抓住**编造或算错的数字**（核心指标容差收紧到 0.5%）；
- 抓住**报告内部自相矛盾**（同一指标前后数值不一致）；
- 抓住**编造引用**（深度研报里的数字必须能在它引用的检索资料中找到）；
- 给出**可核实覆盖率**，并在覆盖率不足时**拒绝显示"通过"**。

**❌ 做不到（请务必知道）**

- 编造的公司名、日期、事件、客户、订单等**非数字内容**；
- **"数字正确但结论错误"**的推理错误；
- **来源本身的真伪**（只校验"数字 ↔ 事实库/引用来源 是否一致"，不校验来源可不可信）；
- **事实库本身出错**时，它会把错误值当作"事实"；
- 事实库覆盖不到的指标（词典外数字会被标为"无来源"，但**不等于**它一定错）。

---

## 🚀 快速开始

### 方式一：本地 Python（推荐）

```bash
git clone https://github.com/sjknjj/research-report-agent.git
cd research-report-agent

pip install -r requirements.txt

cp .env.example .env          # 填入你的 LLM_API_KEY

python main.py demo           # 离线演示：不需要 key、不联网
python main.py brief 600519   # 在线：贵州茅台投研速览
```

### 方式二：Docker

```bash
cp .env.example .env          # 填入你的 LLM_API_KEY
docker-compose up -d
# 打开 http://localhost:8000
```

镜像已安装中文字体（`fonts-noto-cjk`），PDF 中文可正常渲染。

> 环境要求：Python 3.10+。核心功能只需要 `akshare pandas numpy requests`；
> 图表 / Web / PDF 依赖见 `requirements.txt`。

---

## 🖥️ Web 界面（13 个 Tab）

```bash
python main.py serve              # 默认 8000
python main.py serve --port 8080  # 换端口
```

打开 <http://localhost:8000>：

| Tab | 功能 |
|---|---|
| 🏠 **首页** | 公司速览：行情、财务、技术面、交叉校验、产业链图、PDF 导出、自选、行情预警 |
| 📊 **多公司对比** | 多代码横向对比表 + 雷达图 + 估值分位图 |
| 🏭 **行业分析** | 行业成分股 + 板块涨跌聚合 |
| 📝 **深度研报** | 多智能体流水线，**实时**进度；可选搜索引擎与递归深挖 |
| 📢 **公告提炼** | 粘贴公告 → 要点提炼 + 与财报交叉校验 |
| 😊 **情绪面** | 新闻情绪（正面/负面/中性 + 占比） |
| 📈 **回测** | MACD 金叉/死叉策略回测（含手续费、胜率、回撤） |
| ⭐ **因子** | 估值/成长/盈利/动量 4 因子打分（0–10） |
| 📊 **统计** | 历史幻觉率与模型可信度排名 |
| ⭐ **自选股** | 本地存储，支持一键速览与删除 |
| 📅 **每日跑批** | GitHub Actions 定时推送配置教程 |
| 🔑 **API 配置** | 大模型 API Key 申请教程 |
| ⚙️ **设置** | 推送渠道 + 预警阈值（保存到 `output/web_settings.json`，**真正生效**） |

**几个容易误解的点**

- **"自动刷新"只轮询 `/api/quote` 实时行情**，不会重复调用大模型（旧版本会每 10 秒重跑一遍完整
  LLM 流程，挂着页面过夜等于上万次付费调用）；
- **设置页的预警阈值真的被消费**：首页行情超阈值会变琥珀色预警，
  `python main.py monitor` 未传 `--threshold` 时也会读取它；
- 首页勾选的**数据源真的生效**（启用容灾与财务口径交叉验证）。

---

## 🛡️ 反幻觉：四道防线

### 防线总览

| 防线 | 做什么 | 怎么做的 |
|---|---|---|
| **① 输入约束** | 不让模型自由发挥 | 只把清洗后的「事实包」给模型，并明确声明"只能用这些数字" |
| **② 数字校验** | 抓出编造/算错的数字 | 全量抽取报告里**每个**财务数字并与事实库比对；未归因的进「无来源」清单 |
| **③ 语义校验** | 抓出无来源的定性判断 | 让模型把文本拆成原子声明，逐条判断是否有事实包支撑（**在线模式**） |
| **④ 逻辑审校 / 引用溯源** | 抓出逻辑错误与编造引用 | 公司速览走三态三表勾稽 + 内部矛盾检测；深度研报走引用溯源 |

### 数字校验的状态

| 状态 | 含义 |
|---|---|
| `supported` | 命中指标词典且与事实库一致 |
| `mismatch` | 命中指标词典但与事实库矛盾（**疑似幻觉**，会触发自动纠错重写） |
| `unverifiable` | 命中词典但事实库没有该项数据 |
| `implicit` | **数值与事实库一致，只是文中没点名指标**（计入覆盖率，不算无来源） |
| **`unsourced`** | **未能归因到任何指标、也未在事实库中找到对应值 → 明确列出，需人工复核** |

- 指标词典 **25 项**（营收/净利/扣非净利/同比/环比/毛利率/净利率/ROE/EPS/股价/涨跌幅/
  波动率/换手率/区间高低价/**5 日与 20 日均线**/PE/PB/股息率/主力净流入…），
  其中 **12 项为核心指标**；
- 容差**按指标分级**：核心指标 **0.5% / 0.06 个百分点**，其余 **1% / 0.1 个百分点**；
- 支持**千分位**（`1,234.56亿元`）与**中文数字**（`一百五十二点三亿元`、`百分之四十二点三`）；
- 支持**分期归因**（`2024年营收…` 不会与最新期混用）与**环比**（较上期增速）；
- 支持 **Markdown 加粗**（`销售毛利率 **29.99%**`）——模型写报告几乎总是这个格式；
- 支持**表格归因**：按「表头 → 指标、首列 → 报告期」解析 Markdown 表格，
  连表格上方的 `（单位：亿元）` 也会被读进来，
  所以「近五期趋势」这类数字**能真正参与核对**，而不是一律算"无来源"；
- 容差会按**数字写出来的精度**补偿：`0.09`（亿元）只有 ±0.005 亿的显示精度，
  不会因为它"位数少"就判成矛盾。

> 自相矛盾检测按 **(指标, 报告期)** 分组：2026Q3 的 `+869.79%` 与 2025 年报的 `-62.06%`
> 本来就该不同，不会被误报成"同一指标前后不一致"。
>
> 百分比不进 `implicit` 桶：`3.70%` 这种两位小数太容易撞上别的指标（毛利率/ROE），
> 纯靠数值相等就说"已核实"会放过真实的指标错配。

### 三态结论：不再给"假阳性安全感"

旧版只看"有没有矛盾"，于是**一份数字全是"无来源"的报告也会显示"通过"**。现在：

| 结论 | 触发条件 |
|---|---|
| ✅ **通过** | 无矛盾，且覆盖率 ≥60%、无来源 ≤30%、无法核实 ≤50%、无内部矛盾 |
| ⚠️ **通过但有保留（需人工复核）** | 无矛盾，但上述任一指标不达标——报告会**逐条列出原因** |
| ❌ **未通过** | 存在与事实库矛盾的数字 |

同时输出：`coverage`（可核实覆盖率）、`unsourced_ratio`、`contradictions`（内部矛盾）、
`confidence_reasons`（可信度下调原因）。

### 可信度等级

`A（<5%）/ B（5–15%）/ C（15–30%）/ D（>30%）`（按幻觉率），
并按覆盖率、无来源占比、无法核实占比、内部矛盾**自动降级**。

### 三大报表勾稽

核对 `资产 = 负债 + 权益`、`净利润 = 利润总额 − 所得税`、`期末现金 = 期初 + 三类净额` 等恒等式。
结论也是三态：`通过 / 部分通过（有科目未获取，结论不完整）/ 未通过 / 数据不足`——
不会因为抓不全科目就说"通过"。**离线模式直接跳过，不发网络请求。**

### 引用溯源校验（深度研报专用）

深度研报是主题研究，没有标的公司事实库，因此用另一条确定性防线：
**正文里的每个财务数字，必须能在它引用的检索资料里找到**。
覆盖率与"疑似对不上"的数字会写进报告结尾的「🛡️ 反幻觉总览」；
检索结果只有标题/链接（如离线演示）时，如实显示「未执行」。

### 报告结尾的「🛡️ 反幻觉总览」

由**真实执行过**的检查驱动：跑过的显示结果，没跑的显示「⚪ 未启用 + 原因」，
无事实库的路径下可信度显示「未评级」。**不会出现"未执行却声称已启用"。**

---

## 🤖 多智能体流水线（深度研报）

```
① Planner（规划提纲）
   自动生成研究提纲：核心观点 / 行业背景 / 市场规模 / 产业链 / 竞争格局 / 风险 / 结论
        │
② Researcher（检索资料）
   生成 10 条检索词 → 去重搜索 → 抓取网页正文（可开启递归深挖，轮数可配）
        │
③ Analyst（逐章分析）
   每章提炼要点，标注引用来源 [1,2,3…]（并行）
        │
④ Debate（多空辩论）
   多方 vs 空方，双方论点 + 两轮互相反驳（轮数可用 DEBATE_ROUNDS 配置）
        │
⑤ Writer（撰写报告）
   撰写正文 +「多空对照」+「速览仪表盘」+「反幻觉总览」（并行）
        │
⑥ Reviewer（审校修订）
   结构完整性 / 引用区间合法性 / 引用覆盖率（非阻断）/ LLM 逻辑复核 / 引用溯源提示
   覆盖率偏低时打回重写（自动生成章节不计入覆盖率）
        │
   输出最终报告
```

- **实时进度**：Web 端通过 SSE 真实推送每一步（不是跑完才一次性吐出来）；
- **`--deep` 递归深挖**：离线模式下会明确提示"该选项无效"，不静默失效；
- **认证类错误会直接上抛**（无效 key 不会被兜底成"成功"的假报告）。

---

## 💻 CLI 命令

| 命令 | 作用 |
|---|---|
| `python main.py brief 600519` | 公司投研速览 |
| `python main.py report 低空经济` | 深度研报（多智能体） |
| `python main.py compare 600519 000858` | 多公司对比（逗号分隔也支持） |
| `python main.py industry 银行` | 行业成分与板块聚合 |
| `python main.py announce --file notice.txt --symbol 600519` | 公告提炼 + 财报交叉校验 |
| `python main.py daily` | 自选股批量跑 + 推送（配合 GitHub Actions） |
| `python main.py monitor` | 盘中监控，达阈值推送（阈值缺省读设置页） |
| `python main.py history --keyword 600519` | 查看历史报告 |
| `python main.py stats` | 幻觉率统计与模型排名 |
| `python main.py demo` | 离线端到端演示（无需密钥/网络） |
| `python main.py serve --port 8080` | 启动 Web 服务 |

**通用参数**：`--pdf`（额外导出 PDF）、`--md-only`、`--sample`（强制离线样例）、
`--mode auto|online|offline`、`--model`、`--rounds`、`--output`、`--config`、`--log-level`。

---

## ⚙️ 配置

优先级：内置默认 → `config.json` → `.env` → 真实环境变量 → CLI 参数。

### `.env`（常用项，完整见 [.env.example](./.env.example)）

```bash
LLM_MODE=auto                      # auto / online / offline
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-你的key
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=2048
LLM_TIMEOUT=60

SEARCH_PROVIDER=auto               # auto / baidu / bing / duckduckgo / tavily / mock
TAVILY_API_KEY=
SEARCH_MAX_RESULTS=5
SEARCH_MAX_FETCH=3

REPORT_LANGUAGE=zh
REVIEW_MAX_ROUNDS=1
DEEP_SEARCH_ROUNDS=2
DEBATE_ROUNDS=2
OUTPUT_DIR=output
EXPORT_FORMATS=md,html
LOG_LEVEL=INFO

DATA_SOURCES=                      # 留空 = 只用 AKShare 单源
AUTO_FAILOVER=true
CROSS_CHECK_ENABLED=true
MCP_SERVER_URL=
TUSHARE_TOKEN=
JQDATA_USERNAME= / JQDATA_PASSWORD=
RQDATA_USERNAME= / RQDATA_PASSWORD=

RAG_MIN_SCORE=0.15                 # RAG 相关性下限

FEISHU_WEBHOOK_URL= / FEISHU_SECRET=
WECOM_WEBHOOK_KEY=
TELEGRAM_BOT_TOKEN= / TELEGRAM_CHAT_ID=
```

### `config.json`（结构化配置，复制 [config.example.json](./config.example.json)）

```json
{
  "mode": "auto",
  "llm_base_url": "https://api.deepseek.com/v1",
  "llm_model": "deepseek-chat",
  "temperature": 0.3,
  "max_tokens": 2048,
  "request_timeout": 60,

  "search_provider": "auto",
  "max_results_per_query": 5,
  "max_fetch_pages": 3,
  "fetch_content_chars": 2000,

  "language": "zh",
  "max_review_rounds": 1,
  "deep_search_rounds": 2,
  "debate_rounds": 2,
  "output_dir": "output",
  "export_formats": ["md", "html"],
  "log_level": "INFO",

  "rag_min_score": 0.15,

  "data_sources": ["akshare", "sina_http", "tencent_http"],
  "auto_failover": true,
  "cross_check_enabled": true,
  "bypass_system_proxy": true,
  "mcp_server_url": ""
}
```

> 项目**不解析 YAML**，请使用 `config.json`。

任何 **OpenAI 兼容**端点都能用：DeepSeek（默认）、OpenAI、通义千问 DashScope、
智谱 GLM、Kimi、火山方舟、本地 Ollama。

---

## 📊 数据源（12 个）

| 类型 | 数据源 | 实测状态 |
|---|---|---|
| 免费 | **AKShare** | ✅ 默认第 1 顺位；内部还会按东财 → 新浪 → 腾讯降级 |
| 免费 | 新浪财经 HTTP | ✅ 默认第 2 顺位，已实测 |
| 免费 | 腾讯财经 HTTP | ✅ 默认第 3 顺位，已实测 |
| 免费 | 东方财富 HTTP | ✅ 已实测（上游与 akshare 相同，不进默认顺序） |
| 免费 | 离线样例 | ✅ 容灾兜底，已实测 |
| 免费 | **MCP** | ⚙️ 已实现（JSON-RPC 2.0 over HTTP），未与真实外部 Server 联调 |
| 付费 | Tushare | 🔑 需 Token，本仓库无法验证 |
| 付费 | Wind 万得 | 🔑 需终端 + WindPy，无法验证 |
| 付费 | Choice 东方财富 | ⚠️ 需终端 + 官方 SDK；当前适配用的是非官方包名，很可能要改写 |
| 付费 | 同花顺 iFinD | ⚠️ 同上（官方包名可能是 iFinDPy） |
| 付费 | 聚宽 JQData | 🔑 需账号，无法验证 |
| 付费 | 米筐 RiceQuant | 🔑 需账号，无法验证 |

### 两层容灾：先换接口，再换数据源

「一个不行换另一个」在**零配置**下就已经生效——不用先猜到一个配置项它才动。

**第一层：接口级降级**（同一个数据源内部换上游）

| 数据 | 降级顺序 |
|---|---|
| 日线行情 | 东财 `stock_zh_a_hist` → 新浪 `stock_zh_a_daily` → 腾讯 `stock_zh_a_hist_tx` |
| 财务摘要 | 同花顺 `stock_financial_abstract_ths` → 东财 `stock_financial_abstract` → 东财 `stock_financial_analysis_indicator` |
| 个股信息 | 东财 `stock_individual_info_em` → 新浪实时快照 |

**第二层：数据源级容灾**（换整个数据源，按免费源完备度排序）

| 顺序 | 数据源 | 覆盖接口 | 上游 |
|---|---|---|---|
| 1 | `akshare` | 13 类 | 东财 / 同花顺 / 新浪 |
| 2 | `sina_http` | 行情 / 财务 / 信息 | 新浪 |
| 3 | `tencent_http` | 行情 / 信息 | 腾讯 |

> `eastmoney_http` 的日线/财务/信息**与 akshare 用的是同一个东财上游**，
> 同时启用只是重复打一遍同样的请求，所以不进默认顺序（需要时可显式配置）。

```json
{
  "data_sources": ["akshare", "sina_http", "tencent_http"],
  "auto_failover": true,
  "cross_check_enabled": true,
  "bypass_system_proxy": true
}
```

- **留空 `data_sources`** = 就用上面的完备度顺序，不是"只剩 akshare 单源"。
- **容灾切换**：主源失败自动切下一个可用源，并"粘"在可用源上，避免每次重试死掉的主源。
  覆盖**行情与财务**取数（`info/news/valuation/fund_flow` 仍是单主源、尽力而为）。
- **多源交叉验证**：对**财务摘要口径**指标（营收/净利/同比/毛利率/净利率/ROE/PE/PB）比对各源数值，
  相对偏差超阈值时在报告中标记。

### 为什么「我明明选的是 akshare」也会挂

**AKShare 不是数据源，是抓取库**——它内部封装了东财、新浪、腾讯、同花顺十几个网站。
配置里写 `"akshare"` 只表示"用这个库去抓"，抓谁由**函数**决定：

```python
# akshare/stock_feature/stock_hist_em.py  —— 文件名里的 _em 就是 East Money
def stock_zh_a_hist(...):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
```

所以 `akshare` 的日线**就是东财**。东财对高频请求按 IP 限流时会直接 RST（一个字节都不回），
日志里表现为 `RemoteDisconnected`；因为连接走了代理隧道，urllib3 还会把它误报成
`ProxyError`——**代理其实是好的，是隧道另一头断了**。这正是上面第一层降级要解决的问题。

`bypass_system_proxy`（默认 `true`）会把国内财经域名写进 `no_proxy`：
macOS 上 `requests` 会自动读系统代理设置，**即使一个 `HTTP_PROXY` 环境变量都没有**。
只有"必须靠代理才能出网"的环境才需要把它设成 `false`。

### MCP 数据源

```json
{ "data_sources": ["akshare", "mcp"], "mcp_server_url": "http://127.0.0.1:9000/mcp" }
```

客户端按 `initialize → notifications/initialized → tools/call` 调用外部 Server，
工具名约定为 `{tool_prefix}price_daily` / `{tool_prefix}financial_abstract` / `{tool_prefix}stock_info`
（默认前缀 `stock_`）。

---

## 🔌 接口与集成

### Web API

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/brief?symbol=600519` | GET | 公司速览（`sources=` 可选多数据源） |
| `/api/brief/pdf?symbol=600519` | GET | 速览 PDF 下载 |
| `/api/compare?symbols=600519,000858` | GET | 多公司对比（含图表 JSON） |
| `/api/industry?name=银行` | GET | 行业成分与聚合 |
| `/api/announce` | POST | 公告提炼 |
| `/api/sentiment?symbol=600519` | GET | 新闻情绪 |
| `/api/backtest?symbol=600519&days=120` | GET | MACD 策略回测 |
| `/api/factor?symbol=600519` | GET | 4 因子打分 |
| `/api/stats` | GET | 幻觉率统计 |
| `/api/quote?symbol=600519` | GET | 轻量实时行情（供自动刷新） |
| `/api/settings` | GET / POST | 读取 / 保存推送与预警设置 |
| `/api/report` | GET | 深度研报（同步） |
| `/api/report/stream?topic=…` | GET | 深度研报（SSE **实时**进度） |
| `/mcp` | POST | MCP 协议入口（JSON-RPC over HTTP） |
| `/mcp/tools` | GET | 查看 MCP 工具列表 |

### MCP Server

**方式一：stdio（Claude Desktop / Cursor 推荐）**

```json
{
  "mcpServers": {
    "research-report-agent": {
      "command": "python",
      "args": ["/绝对路径/research-report-agent/mcp_server.py"]
    }
  }
}
```

自测：

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp_server.py
```

**方式二：HTTP** — `python main.py serve` 后，`POST /mcp` 是裸 JSON-RPC over HTTP
（不含 session/SSE），适合 curl 与自建客户端。

**可用工具**

| 工具 | 参数 | 说明 |
|---|---|---|
| `get_stock_brief` | `symbol`（必填） | 公司投研速览（含反幻觉校验） |
| `verify_numbers` | `text`、`symbol`（均必填） | 数字反幻觉校验 |
| `search_web` | `query`（必填）、`max_results`（可选） | 网页检索（离线回退 mock） |
| `generate_stock_report` | `topic`（必填）、`deep`（可选） | 生成完整研报 |

已实现握手所需方法：`initialize`、`notifications/initialized`（不回响应）、`ping`、
`tools/list`、`tools/call`。

---

## 📈 可视化图表

| 图表 | 在哪里 | 说明 |
|---|---|---|
| **K 线图** | 公司速览 | 日线 K 线 + 成交量 + 均线 |
| **财务趋势图** | 公司速览 | 营收 / 净利润柱状 + **毛利率折线（右轴）** |
| **PE-PB 仪表盘** | 公司速览 | 估值水平 |
| **产业链交互式图谱** | 公司速览 | 上游/中游/下游三层网络图（14 条产业链；未装 plotly 回退 matplotlib） |
| **多公司对比雷达图** | 多公司对比 | 5 维度（估值/营收增长/利润增长/盈利能力/技术面） |
| **估值分位图** | 多公司对比 | PE/PB 在同侪中的分位（"便宜于多少比例的同侪"） |

导出 HTML 会**内联 plotly.js**（离线可看，不依赖 CDN）；图表只在有对应数据时生成。

---

## 📬 推送与自动化

| 渠道 | 配置 |
|---|---|
| **飞书自定义机器人** | `FEISHU_WEBHOOK_URL`（+ 可选 `FEISHU_SECRET`，HMAC-SHA256 签名） |
| **企业微信机器人** | `WECOM_WEBHOOK_KEY` |
| **Telegram Bot** | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` |

- 推送内容：公司/代码、最新价与涨跌幅、速览要点、交叉校验结论、报告链接；
- 每个渠道独立隔离：一个渠道失败**不会**中断其它渠道；
- **每日自选股**：编辑 `watchlist.txt` → `python main.py daily`；
  `.github/workflows/daily-report.yml` 已配好（工作日 09:00 北京时间 = UTC 01:00），
  缺少必需 Secret 时会直接失败并给出明确错误，不会拿空模型名去跑。

---

## 📁 项目结构

```
research-report-agent/
├── main.py                  # CLI 入口（11 个子命令）
├── mcp_server.py            # MCP stdio 入口（Claude Desktop 直接用）
├── config.example.json      # 结构化配置示例
├── .env.example             # 环境变量示例
├── watchlist.txt            # 自选股列表
├── src/
│   ├── data/                # 数据抓取 / 清洗 / 多数据源
│   │   ├── sources.py       #   12 个数据源适配器
│   │   ├── manager.py       #   多源管理器（容灾 + 交叉验证）
│   │   ├── data_service.py  #   统一数据入口（缓存 / 回退）
│   │   ├── cleaner.py       #   字段映射与清洗
│   │   └── cache.py         #   按天缓存（含清洗后的 DataFrame）
│   ├── agent/               # 多智能体流水线
│   │   ├── planner.py / researcher.py / analyst.py
│   │   ├── debate.py        #   多空辩论（两轮反驳）
│   │   ├── writer.py        #   撰写 + 反幻觉总览 + 引用溯源
│   │   ├── reviewer.py      #   审校（结构/引用/逻辑）
│   │   ├── orchestrator.py  #   编排与进度回调
│   │   └── base.py          #   健壮 JSON 解析 + 认证错误上抛
│   ├── verify/              # 反幻觉校验（核心）
│   │   ├── facts.py         #   事实库（输入侧锁定，含分期与环比）
│   │   ├── numbers.py       #   通用数字抽取/比较内核（含中文数字）
│   │   ├── verifier.py      #   数字交叉校验 + 三态结论 + 矛盾检测
│   │   ├── citation.py      #   引用溯源校验（深度研报）
│   │   ├── semantic_check.py#   语义层 faithfulness 校验
│   │   ├── statements.py    #   三大报表勾稽（三态 + 字段诊断）
│   │   └── stats.py         #   幻觉率历史统计
│   ├── analysis/            # 技术面 / 产业链 / 对比 / 回测 / 情绪
│   ├── rag/                 # 本地向量检索（含相关性阈值）
│   ├── tools/               # 网页检索 / 正文抓取
│   ├── notify/              # 飞书 / 企微 / Telegram
│   ├── mcp/                 # MCP Server 实现
│   └── export/              # Markdown / HTML / PDF 导出
├── web/app.py               # FastAPI 服务 + 内嵌前端（13 Tab）
├── data/samples/            # 离线样例数据（含行业样例）
├── examples/                # 4 个可运行示例
└── tests/                   # 288 项单元测试（全部离线）
```

---

## 🧪 测试

```bash
python -m pytest tests/ -q      # 288 项，全部离线，不需要网络与 API Key
```

覆盖：反幻觉（数字抽取/中文数字/容差/三态/矛盾/引用溯源）、数据清洗与缓存、
多源容灾与 MCP 客户端、回测/对比/情绪/指标/产业链、多空辩论、推送签名、
导出与 CLI、Web API。

---

## 🚧 已知限制

| 项 | 状态 |
|---|---|
| Wind / Choice / iFinD / JQData / RiceQuant / Tushare | 需外部授权，**本仓库无法验证**；Choice/iFinD 适配用了非官方包名，很可能需改写 |
| MCP 数据源 | 协议与代码已实现并有离线测试，**未与真实 Server 联调** |
| 推送渠道 | 签名与请求构造已验证，**未对接真实 webhook**（需你的 URL） |
| GitHub Actions | 工作流已修正，**未在真实仓库执行过** |
| Docker 镜像 | Dockerfile 已修正（含中文字体），**未在真实环境构建验证** |
| 语义层校验 | 依赖在线 LLM，离线不执行 |
| RAG | 已加相关性阈值，但**来源本身的真伪**不校验 |

---

## ⚠️ 免责声明

本项目仅供学习与研究使用，**不构成任何投资建议**。
生成的报告可能存在幻觉或错误，**关键数据请自行核实**。
股市有风险，投资需谨慎。作者不对使用本项目产生的任何损失负责。

## 📜 开源协议

GNU AGPL v3.0
