# 使用教程

从零跑通本项目的完整步骤。没密钥、没网络也能先用离线样例体验。

---

## 一、环境准备

**要求：** Python 3.9+（推荐 3.10/3.11）

```bash
# 1. 克隆项目
git clone https://github.com/sjknjj/research-report-agent.git
cd research-report-agent

# 2. 创建虚拟环境（推荐，避免污染系统 Python）
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt
```

依赖说明：

| 依赖 | 用途 | 是否必须 |
| --- | --- | --- |
| `akshare` / `pandas` / `numpy` | 抓 A 股数据、清洗、技术指标计算 | 必须 |
| `requests` | 大模型接口、网页抓取 | 必须 |
| `plotly` | K线图、财务趋势图、PE/PB 仪表盘 | 可选（没装会自动跳过图表） |
| `fastapi` / `uvicorn` | Web 服务 | 可选（只用 CLI 可不装） |

---

## 二、配置大模型密钥

在线模式需要一个 OpenAI 兼容接口的密钥，DeepSeek、豆包、通义、Kimi 都可以。

```bash
cp .env.example .env
```

然后编辑 `.env`，以 DeepSeek（便宜，适合长文）为例：

```bash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-你的真实密钥
```

> 其他厂商的 `LLM_BASE_URL` / `LLM_MODEL` 在 `.env.example` 里都有现成示例，取消注释改密钥即可。

**不配密钥也能跑**：所有命令加 `--sample`（或用 `--mode offline`），会用内置虚构样例数据走完整流程，适合先验证环境。

---

## 三、最快上手：一键离线 Demo

```bash
python main.py demo
```

这一条命令会依次演示三个场景，无需密钥、无需联网：

1. **公司投研速览**：数据清洗 → 事实锁定 → 大模型生成 → 数字交叉校验
2. **反幻觉演示**：故意写 3 个错误数字，看校验器如何抓出并给出正确值
3. **公告要点提炼**：公告数字与财报交叉校验

跑完产物会输出到 `output/` 目录。

---

## 四、七大命令详解

### 1. 公司投研速览 `brief`（核心功能）

抓取某只股票的行情、财务、资金流、估值、龙虎榜，结合**技术面信号**和**产业链位置**，生成带反幻觉校验的速览报告。

```bash
# 在线：真实抓取 + 大模型（贵州茅台）
python main.py brief 600519

# 指定行情区间
python main.py brief 600519 --start 20250101 --end 20251231

# 离线样例（无需密钥/网络）
python main.py brief 000000 --sample
```

报告里会包含：
- 财务表现、行情表现、资讯梳理、风险提示
- **技术面信号**：MACD 金叉/死叉、RSI 超买超卖、KDJ、布林带、均线排列
- **产业链全景**：若公司在内置 14 条产业链中，自动列出上下游对标公司
- 数字交叉校验表 + 事实包附录（每个数字可溯源）

### 2. 公告要点提炼 `announce`

```bash
# 用内置样例公告
python main.py announce --sample

# 喂自己的公告文本，并与某公司财报交叉校验
python main.py announce --file 公告.txt --title 2025年年度业绩快报 --symbol 600519
```

### 3. 行业信息汇总 `industry`

```bash
python main.py industry 白酒
```

聚合板块成分股的涨跌、市值，再由大模型写成行业概述。

### 4. 通用多智能体研报 `report`

对任意主题（不限股票）跑「规划→检索→分析→撰写→审校」多智能体流水线。

```bash
python main.py report 低空经济产业链分析

# 开启递归深挖：初检后让 LLM 分析知识缺口，自动补检索 2 轮
python main.py report 低空经济产业链分析 --deep
```

### 5. 多公司横向对比 `compare`

输入多家公司，自动抓取核心指标（PE/PB/营收及净利增速/毛利率/ROE/区间涨跌/技术面），生成横向对比表并按指定维度排序。

```bash
# 对比贵州茅台与五粮液，默认按净利润同比排序
python main.py compare 600519 000858

# 按市盈率从低到高排序（估值便宜优先）
python main.py compare 600519 000858 000568 --by pe

# 离线样例
python main.py compare 000000 000001 --sample
```

`--by` 可选维度：`profit_yoy`（净利同比）、`revenue_yoy`（营收同比）、`net_profit`（净利润）、`pe`/`pb`（估值，升序）、`roe`、`period_return`（区间涨跌）。

### 6. Web 服务 `serve`

```bash
# 先装 Web 依赖（若没装）
pip install fastapi uvicorn

# 启动
python main.py serve
```

浏览器打开 http://localhost:8000 ，在页面上输入股票代码或研究主题，即可在线生成公司速览 / 深度研报。

### 7. 导出 PDF

任何命令后加 `--pdf`，会在 Markdown / HTML 之外额外生成一份中文 PDF（自动识别 macOS/Windows/Linux 中文字体）：

```bash
python main.py brief 600519 --pdf
python main.py compare 600519 000858 --by pe --pdf
```

> 首次用需 `pip install reportlab`。

### 8. 离线 Demo `demo`

见上方第三节。

---

## 五、通用参数

| 参数 | 说明 |
| --- | --- |
| `--mode {auto,online,offline}` | 强制在线/离线；auto 为有密钥即在线 |
| `--model <模型名>` | 临时覆盖 `.env` 里的模型 |
| `--output <目录>` | 自定义报告输出目录（默认 `output/`） |
| `--rounds <N>` | 数字校验失败后的最大重写轮数 |
| `--md-only` | 只导出 Markdown，不生成 HTML |
| `--pdf` | 额外导出 PDF（中文排版，需 reportlab） |
| `--sample` | 强制用离线样例（仅 brief / announce / compare） |
| `--deep` | 递归深挖检索（仅 report） |
| `--by <维度>` | 多公司对比的排序维度（仅 compare） |
| `--log-level DEBUG` | 打印详细日志，方便排查 |

---

## 六、作为 Python 库调用

```python
from src.common.config import load_settings
from src.services.research_assistant import ResearchAssistant

settings = load_settings()
assistant = ResearchAssistant(settings)

# 公司速览（内部自动完成：抓取→清洗→事实包→技术指标→产业链→大模型→交叉校验）
result = assistant.company_brief("600519")
print(result.text)

# 技术面信号 / 产业链信息
print(result.extra["technicals"])   # MACD/RSI/KDJ/布林带信号
print(result.extra["chain"])        # 所属产业链
```

单独使用技术指标 / 产业链 / RAG：

```python
import pandas as pd
from src.analysis.indicators import analyze_technicals
from src.analysis.chain import find_chain, chain_to_text
from src.rag.vector_store import VectorStore

# 技术指标（传入含 open/high/low/close 列的 DataFrame）
signals = analyze_technicals(price_df)
print(signals["signals"])

# 产业链查询
chain = find_chain("300750")
print(chain_to_text(chain))

# RAG 向量检索
store = VectorStore(persist_path="data/rag_index.json")
store.add("宁德时代 2025 年营收增长，动力电池装机量全球第一", source="宁德时代报告")
for hit in store.search("动力电池龙头", top_k=3):
    print(hit["score"], hit["text"])
```

**三大报表勾稽校验：**

```python
from src.verify.statements import check_statements

report = check_statements(
    balance={"total_assets": 1000, "total_liabilities": 600, "total_equity": 400},
    income={"total_profit": 200, "income_tax": 50, "net_profit": 150,
            "revenue": 1000, "operating_cost": 700, "operating_profit": 180},
    cashflow={"begin_cash": 100, "end_cash": 150,
              "net_op_cash": 120, "net_inv_cash": -50, "net_fin_cash": -20},
)
print(report.render_text())   # 资产=负债+权益、净利润勾稽、现金流勾稽、利润质量
print(report.passed)          # 是否存在硬错误
```

**多公司对比：**

```python
from src.analysis.comparison import ComparisonService

svc = ComparisonService()
rows = svc.compare(["600519", "000858"])          # 在线抓取
ranked = svc.rank_table(rows, by="pe")            # 按市盈率排序
print(svc.render_markdown(rows, by="pe"))         # 输出 Markdown 对比表
```

---

## 七、开启真实 Embedding（可选，提升 RAG 检索质量）

RAG 功能默认用本地哈希向量，**零依赖即可用**。想获得更准的语义召回，可在 `.env` 里配置 OpenAI 兼容的 embedding 接口：

```bash
EMBED_MODEL=text-embedding-3-small
EMBED_BASE_URL=https://api.openai.com/v1
EMBED_API_KEY=sk-你的key
```

`EMBED_BASE_URL` / `EMBED_API_KEY` 留空时会自动复用 `LLM_BASE_URL` / `LLM_API_KEY`；接口不通则自动回退本地哈希向量，RAG 始终可用。

---

## 八、查看输出

所有报告默认输出到 `output/`：

- `*.md`：Markdown 版报告
- `*.html`：自包含 HTML（内嵌 Plotly K线图、财务图、校验表），双击即可在浏览器打开，可直接发人

---

## 九、运行测试

```bash
python3 -m unittest discover -s tests -v
```

共 21 项，覆盖数据清洗、反幻觉校验、LLM 客户端、助手编排、研报流水线。

---

## 十、常见问题

**Q：提示「未检测到 LLM_API_KEY，已进入离线演示模式」？**
A：没配密钥或密钥没生效。想在线用就先配好 `.env`；想先体验就加 `--sample`。

**Q：AKShare 抓数据失败 / 超时？**
A：公开接口有限流，程序会自动重试，关键数据失败会回退离线样例并明确标注，不会把样例冒充真实数据。

**Q：报告里没有图表？**
A：确认装了 plotly（`pip install plotly`）；离线样例数据较少时也可能不生成。

**Q：`serve` 命令报 ModuleNotFoundError？**
A：`pip install fastapi uvicorn`。

**Q：想换模型厂商？**
A：改 `.env` 里的 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 三行即可，代码无需改动。
