# 研报辅助 Agent · Research Report Agent

**最靠谱的反幻觉 A 股投研 Agent**  
基于大模型 + 多数据源，自动生成投研报告，系统性规避 AI「数字幻觉」。

> 📖 **完整使用教程**：[USAGE.md](./USAGE.md)（从安装到所有命令，一步一步教你用）

---

## ✨ 核心亮点：反幻觉三道防线

大模型最危险的错误是「一本正经地编造财务数字」。本项目**不依赖模型自觉**，而是用确定性校验兜底：

| 防线 | 做什么 | 效果 |
|---|---|---|
| **① 输入约束** | 只让模型看清洗后的「事实包」，不让它自由发挥 | 从源头减少编造 |
| **② 数字校验** | 报告里每个数字都跟真实数据交叉核对 | 抓出编造的数字 |
| **③ 逻辑审校** | 检查因果关系、引用覆盖率、三表勾稽 | 抓出逻辑错误 |

实际效果：模型把营收写成 182.30 亿（真实是 152.30 亿），校验器精确抓出并自动纠错。

---

## 🚀 能做什么

### 1. 公司速览（一键生成）
输入股票代码，自动生成速览报告：行情、财务、技术面、交叉校验、反幻觉总览。

### 2. 深度研报（多智能体流水线）
输入研究主题，自动生成完整研报：规划 → 检索 → 分析 → 多空辩论 → 撰写 → 审校。

### 3. 多公司对比（横向排名）
输入多个股票代码，自动横向对比：PE/PB/增速/ROE/技术面，自动排序 + 雷达图。

### 4. 公告提炼
把公告文本丢进去，自动提炼要点 + 跟财报交叉校验。

### 5. 每日自选股推送
配一次之后，每天自动收到飞书/企业微信通知，不用打开任何页面。

---

## 📊 多数据源架构（12个内置）

| 类型 | 数据源 | 怎么用 |
|---|---|---|
| **免费** | AKShare | 默认启用 |
| **免费** | 东方财富 HTTP | 自动启用 |
| **免费** | 新浪财经 HTTP | 自动启用 |
| **免费** | 腾讯财经 HTTP | 自动启用 |
| **付费** | Tushare | 配 `TUSHARE_TOKEN` 就能用 |
| **付费** | Wind 万得 | 装 Wind 终端 + WindPy |
| **付费** | Choice 东方财富 | 装 Choice 终端 + SDK |
| **付费** | 同花顺 iFinD | 装 iFinD 终端 + SDK |
| **付费** | 聚宽 JQData | 配账号密码 |
| **付费** | 米筐 RiceQuant | 配账号密码 |
| **其他** | MCP | 接外部 MCP Server |
| **其他** | 离线样例 | 容灾用 |

**多源交叉验证**：选多个数据源后，系统自动比对同一个数字，不一致就标记「⚠️ 数据不一致」。

---

## 🔧 其他能力

- **技术面量化**：MACD/RSI/KDJ/布林带自动计算，金叉死叉、超买超卖信号
- **产业链图谱**：14 条主流产业链，自动定位上下游 + 交互式网络图
- **三大报表勾稽校验**：资产=负债+权益、净利润=利润总额-所得税等恒等式自动核对
- **RAG 历史报告检索**：生成报告前自动检索历史相似案例
- **报告导出**：Markdown / HTML / PDF 三种格式
- **可视化图表**：Plotly 交互式 K线图/财务趋势图/PE-PB 仪表盘/雷达图
- **MCP 接口**：可被 Claude Desktop / Cursor 直接接入
- **多 LLM 支持**：一键切换 OpenAI / DeepSeek / 通义千问 / 智谱等

---

## 🏃 快速开始（3分钟跑起来）

### 第一步：装依赖

```bash
git clone https://github.com/sjknjj/research-report-agent.git
cd research-report-agent
pip install -r requirements.txt
```

### 第二步：配密钥

```bash
cp .env.example .env
# 编辑 .env，填你的 LLM_API_KEY 和 LLM_BASE_URL
```

### 第三步：跑一下看看效果

```bash
# 离线演示模式（不用配密钥，直接跑）
python main.py demo

# 在线模式（配好密钥后）
python main.py brief 600519  # 贵州茅台速览
```

---

## 🖥️ 使用方式

### 方式一：Web 界面（最直观，推荐新手）

```bash
python main.py serve
```

浏览器打开 http://localhost:8000 ，三个 Tab：
- **📊 公司速览**：输入股票代码 → 一键生成速览
- **📝 深度研报**：输入研究主题 → 多智能体流水线生成
- **⭐ 我的自选股**：浏览器本地存储自选股列表

### 方式二：CLI 命令（最灵活，推荐进阶用户）

```bash
# 公司速览
python main.py brief 600519

# 深度研报
python main.py report 低空经济产业链分析

# 多公司对比
python main.py compare 600519 000858 000568

# 公告提炼
python main.py announce "这里粘贴公告文本"

# 启动 Web 服务
python main.py serve

# 查看历史报告
python main.py history

# 盘中实时监控（达涨跌幅阈值自动推送）
python main.py monitor

# 查看幻觉率统计
python main.py stats
```

### 方式三：每日自选股自动推送（最省心）

1. 在飞书/企业微信群里添加自定义机器人，拿到 webhook 地址
2. 填到 `.env` 里
3. 编辑 `watchlist.txt`，一行一个股票代码
4. 跑 `python main.py daily` 测试
5. 用 GitHub Actions 定时自动跑（已经配好了，每个工作日早上 9 点自动运行）

---

## 📁 项目结构

```
research-report-agent/
├── main.py                  # CLI 入口
├── config.example.yaml      # 配置文件示例
├── .env.example             # 环境变量示例
├── watchlist.txt            # 自选股列表
├── src/
│   ├── data/                # 数据抓取 + 清洗 + 多数据源
│   │   ├── sources.py       #   12个数据源适配器
│   │   ├── manager.py        #   多数据源管理器（容灾+交叉验证）
│   │   └── data_service.py   #   数据服务封装
│   ├── agent/                # 多智能体流水线
│   │   ├── planner.py        #   规划提纲
│   │   ├── researcher.py     #   递归深挖检索
│   │   ├── analyst.py        #   逐章分析
│   │   ├── writer.py         #   撰写报告
│   │   ├── reviewer.py       #   审校修订
│   │   └── debate.py         #   多空辩论
│   ├── verify/               # 反幻觉校验
│   │   ├── verifier.py       #   数字交叉校验
│   │   ├── semantic.py       #   语义层 faithfulness 校验
│   │   ├── statements.py     #   三大报表勾稽校验
│   │   └── stats.py          #   幻觉率历史统计
│   ├── analysis/             # 技术面 + 产业链 + 多公司对比
│   │   ├── indicators.py    #   MACD/RSI/KDJ/布林带信号
│   │   ├── chain.py          #   14 条产业链上下游图谱
│   │   ├── chain_viz.py      #   交互式产业链图
│   │   └── comparison.py     #   多公司横向对比 + 雷达图
│   ├── rag/                  # RAG 向量检索
│   ├── tools/                # 网页检索 / 抓取
│   ├── notify/               # 飞书 / 企业微信 / Telegram 推送
│   ├── mcp/                  # MCP Server 接口
│   └── export/               # Markdown / HTML / PDF 导出
├── web/
│   └── app.py                # FastAPI Web 服务
├── data/samples/             # 离线样例数据
├── examples/                 # 案例脚本
├── tests/                    # 21 项单元测试
└── USAGE.md                  # 完整使用教程
```

---

## 📜 开源协议

GNU AGPL v3.0

---

> 💡 **有问题？** 看 [USAGE.md](./USAGE.md) 详细教程，或者提 Issue。
