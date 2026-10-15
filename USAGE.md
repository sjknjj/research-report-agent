# 使用教程 · USAGE.md

从 0 开始，一步一步教你用这个项目。

---

## 目录

1. [安装与配置](#一安装与配置)
2. [快速上手](#二快速上手)
3. [Web 界面使用](#三web-界面使用最直观)
4. [CLI 命令详解](#四cli-命令详解)
5. [多数据源配置](#五多数据源配置)
6. [每日自选股推送](#六每日自选股推送最省心)
7. [反幻觉统计](#七反幻觉统计)
8. [常见问题](#八常见问题)

---

## 一、安装与配置

### 1.1 克隆代码

```bash
git clone https://github.com/sjknjj/research-report-agent.git
cd research-report-agent
```

### 1.2 装依赖

```bash
pip install -r requirements.txt
```

> 💡 建议用虚拟环境（venv / conda），避免污染系统环境。

### 1.3 配密钥

```bash
cp .env.example .env
```

编辑 `.env` 文件，填你的大模型 API：

```bash
# 大模型配置（任选一个）
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-你的key
LLM_MODEL=deepseek-chat

# 也可以用 OpenAI / 通义千问 / 智谱清言等，只要是 OpenAI 兼容接口就行
```

> 💡 **不配密钥也能跑**：会自动进入「离线演示模式」，用样例数据走通流程，只是分析结果不是真实的。

---

## 二、快速上手

### 2.1 第一个命令（离线演示）

不用配密钥，直接跑：

```bash
python main.py demo
```

你会看到：
- 自动生成一份贵州茅台的投研速览（用样例数据）
- 自动跑数字交叉校验
- 输出 Markdown + HTML 报告到 `output/` 目录

### 2.2 第二个命令（真实数据）

配好密钥后，跑真实数据：

```bash
python main.py brief 600519
```

这会：
1. 从 AKShare 抓贵州茅台的真实行情和财务数据
2. 清洗数据
3. 调用大模型生成投研速览
4. 自动跑数字交叉校验 + 语义校验
5. 输出报告到 `output/`

---

## 三、Web 界面使用（最直观）

### 3.1 启动 Web 服务

```bash
python main.py serve
```

浏览器打开 http://localhost:8000

### 3.2 三个 Tab 怎么用

| Tab | 功能 | 适合谁 |
|---|---|---|
| **📊 公司速览** | 输入股票代码 → 一键生成速览 | 日常快速看盘 |
| **📝 深度研报** | 输入研究主题 → 多智能体生成完整研报 | 深度研究 |
| **⭐ 我的自选股** | 保存自选股列表，点击快速生成 | 关注多只股票的人 |

### 3.3 公司速览页详解

1. 输入股票代码（比如 `600519` 或 `000858`）
2. 下面的「数据源选择」面板，勾选想用哪些（免费的直接选，付费的配好 token 再选）
3. 点「生成速览」
4. 等几秒钟，报告就出来了

报告会包含：
- 📊 速览仪表盘（评分、综合判断、核心驱动、主要风险）
- 📈 行情与财务数据
- 🔧 技术面信号
- 🛡️ 反幻觉总览（数字校验结果、可信度等级）
- 📋 三大报表勾稽校验（如果抓得到）
- 🔗 产业链图谱（交互式）

---

## 四、CLI 命令详解

### 4.1 公司速览（brief）

**作用**：输入股票代码，一键生成公司投研速览报告。

**基本用法**：
```bash
python main.py brief 600519
```

**所有参数**：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `symbol` | 股票代码（必填） | 无 |
| `--start-date` | 开始日期（YYYY-MM-DD） | 一年前 |
| `--end-date` | 结束日期（YYYY-MM-DD） | 今天 |
| `--pdf` | 顺便导出 PDF | 关闭 |
| `--push` | 生成完自动推送到飞书/企微 | 关闭 |
| `--mode {auto,online,offline}` | 数据模式 | auto（自动选） |
| `--model` | 用哪个模型 | 配置文件里的默认值 |
| `--output` | 输出目录 | output/ |

**示例**：
```bash
# 贵州茅台速览
python main.py brief 600519

# 五粮液速览，顺便导出 PDF
python main.py brief 000858 --pdf

# 泸州老窖速览，生成完自动推送到飞书
python main.py brief 000568 --push

# 指定时间范围
python main.py brief 600519 --start-date 2024-01-01 --end-date 2024-12-31

# 离线模式（不用密钥）
python main.py brief 600519 --mode offline
```

**输出**：
- `output/600519_brief.md`：Markdown 格式报告
- `output/600519_brief.html`：HTML 格式报告（含图表）
- `output/600519_brief.pdf`：PDF 格式报告（加了 --pdf 才会生成）

---

### 4.2 深度研报（report）

**作用**：输入研究主题，多智能体流水线生成完整研报。

**基本用法**：
```bash
python main.py report 低空经济产业链分析
```

**所有参数**：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `topic` | 研究主题（必填） | 无 |
| `--deep` | 开启递归深挖（多轮补检知识缺口） | 关闭 |
| `--mode {auto,online,offline}` | 数据模式 | auto |
| `--model` | 用哪个模型 | 配置文件里的默认值 |
| `--output` | 输出目录 | output/ |

**示例**：
```bash
# 低空经济产业链分析
python main.py report 低空经济产业链分析

# 开启递归深挖（更深入，但更慢）
python main.py report 低空经济产业链分析 --deep

# 半导体行业深度研究
python main.py report 半导体行业投资价值分析
```

**流水线流程**：
1. **① 规划提纲**：大模型自动生成研究提纲
2. **② 检索资料**：自动搜索相关资料
3. **③ 逐章分析**：每章自动提炼要点
4. **④ 多空辩论**：多方 vs 空方对抗分析
5. **⑤ 撰写报告**：自动写成完整报告
6. **⑥ 审校修订**：自动审校 + 修订

---

### 4.3 多公司对比（compare）

**作用**：输入多个股票代码，横向对比，自动排序 + 雷达图。

**基本用法**：
```bash
python main.py compare 600519 000858 000568
```

**所有参数**：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `symbols` | 股票代码列表（至少 2 个） | 无 |
| `--by` | 按哪个维度排序 | profit_yoy（净利同比） |
| `--mode {auto,online,offline}` | 数据模式 | auto |

**支持的排序维度**：

| 维度 | 说明 |
|---|---|
| `profit_yoy` | 净利润同比（默认） |
| `revenue_yoy` | 营收同比 |
| `net_profit` | 净利润绝对值 |
| `pe` | 市盈率（越低越排前） |
| `pb` | 市净率（越低越排前） |
| `roe` | ROE（越高越排前） |

**示例**：
```bash
# 白酒三巨头对比
python main.py compare 600519 000858 000568

# 按 ROE 排序
python main.py compare 600519 000858 000568 --by roe

# 按市盈率排序（越低越便宜）
python main.py compare 600519 000858 000568 --by pe
```

**输出**：
- 📊 对比表格（PE/PB/增速/ROE/技术面）
- 🕸️ 雷达图（5维度打分）
- 📊 估值分位图（PE/PB 柱状图）

---

### 4.4 公告提炼（announce）

**作用**：把公告文本丢进去，自动提炼要点 + 跟财报交叉校验。

**基本用法**：
```bash
python main.py announce "这里粘贴公告文本"
```

**所有参数**：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `text` | 公告文本（必填） | 无 |
| `--title` | 公告标题 | 公司公告 |
| `--symbol` | 关联股票代码（用于交叉校验） | 无 |
| `--mode {auto,online,offline}` | 数据模式 | auto |

**示例**：
```bash
# 提炼公告
python main.py announce "贵州茅台2023年营收1505亿，同比增长18%"

# 关联股票代码，自动跟财报交叉校验
python main.py announce "贵州茅台2023年营收1505亿，同比增长18%" --symbol 600519 --title "2023年年报公告"
```

**自动做什么**：
- 提炼公告要点
- 跟财报交叉校验数字
- 标记「公告说的数字跟财报对不对得上」

---

### 4.5 其他命令

#### 历史报告（history）

```bash
python main.py history                    # 列出所有历史报告
python main.py history --keyword 600519   # 按关键词搜索
```

#### 盘中实时监控（monitor）

```bash
python main.py monitor                     # 默认 ±5%，60秒轮询
python main.py monitor --threshold 3       # ±3% 就推送
python main.py monitor --interval 30      # 30 秒轮询一次
```

#### 幻觉率统计（stats）

```bash
python main.py stats                       # 查看幻觉率历史统计和模型可信度排名
```

#### 启动 Web 服务（serve）

```bash
python main.py serve                       # 默认 http://127.0.0.1:8000
```

#### 离线演示（demo）

```bash
python main.py demo                        # 用样例数据跑一遍，不用密钥
```

---

## 五、多数据源配置

### 5.1 Web 界面选（最方便）

打开 Web 界面 → 公司速览页 → 勾选想用的数据源 → 生成速览。

**数据源说明**：

| 数据源 | 免费/付费 | 怎么用 |
|---|---|---|
| **AKShare** | 免费 | 默认启用，不用配 |
| **东方财富 HTTP** | 免费 | 自动启用 |
| **新浪财经 HTTP** | 免费 | 自动启用 |
| **腾讯财经 HTTP** | 免费 | 自动启用 |
| **Tushare** | 付费 | 配 `TUSHARE_TOKEN` 就能用 |
| **Wind 万得** | 付费 | 装 Wind 终端 + WindPy |
| **Choice 东方财富** | 付费 | 装 Choice 终端 + SDK |
| **同花顺 iFinD** | 付费 | 装 iFinD 终端 + SDK |
| **聚宽 JQData** | 付费 | 配账号密码 |
| **米筐 RiceQuant** | 付费 | 配账号密码 |
| **MCP** | 免费 | 接外部 MCP Server |
| **离线样例** | 免费 | 容灾用，抓不到真实数据时自动回退 |

### 5.2 配置文件选（批量/定时任务用）

编辑 `config.yaml`：

```yaml
data_source:
  sources:
    - akshare        # 免费，默认启用
    - eastmoney_http # 免费，东财HTTP
    - sina_http      # 免费，新浪财经
    - tencent_http   # 免费，腾讯财经
    # - tushare      # 付费，配好TUSHARE_TOKEN就能用
    # - wind         # 付费，需要Wind终端
    # - choice       # 付费，需要Choice终端
    # - ifind        # 付费，需要iFinD终端
    # - jqdata       # 付费，需要聚宽账号
    # - ricequant    # 付费，需要米筐账号
  auto_failover: true           # 主挂了自动切下一个
  cross_check_enabled: true     # 多源交叉验证
  cross_check_tolerance: 0.01    # 误差阈值（1%），超过就标记不一致
```

### 5.3 付费数据源详细配置

#### Tushare（最推荐的付费数据源）

**特点**：注册就送 100 积分，基本够用；积分越高能调的接口越多。

**怎么配**：
1. 去 https://tushare.pro 注册账号
2. 拿到你的 token
3. 在 `.env` 里加：
   ```bash
   TUSHARE_TOKEN=你的tushare token
   ```
4. Web 界面勾选 Tushare 就能用

#### 聚宽 JQData

**特点**：量化圈子常用，数据全。

**怎么配**：
1. 去 https://www.joinquant.com 注册账号
2. 拿到你的账号和密码
3. 在 `.env` 里加：
   ```bash
   JQDATA_USERNAME=你的聚宽账号
   JQDATA_PASSWORD=你的聚宽密码
   ```
4. Web 界面勾选 JQData 就能用

#### 米筐 RiceQuant

**特点**：跟聚宽类似，也是量化数据源。

**怎么配**：
1. 去 https://www.ricequant.com 注册账号
2. 拿到你的账号和密码
3. 在 `.env` 里加：
   ```bash
   RQDATA_USERNAME=你的米筐账号
   RQDATA_PASSWORD=你的米筐密码
   ```
4. Web 界面勾选 RiceQuant 就能用

#### Wind / Choice / iFinD（商业终端）

**特点**：机构用的付费终端，数据最准最全，但要装对应软件。

**怎么配**：
1. 装对应终端（Wind 万得 / Choice 东方财富 / 同花顺 iFinD）
2. 登录终端
3. 装对应的 Python SDK（WindPy / choice-sdk / ifind-sdk）
4. Web 界面勾选就能用（不用配 token，终端登录了就行）

### 5.4 多源交叉验证效果

选多个数据源后，系统会自动比对同一个数字：

```
⚠️ 数据不一致：市盈率（数据源1=25.3，数据源2=26.1）最大最小误差 3.1%
```

**这就是反幻觉的终极形态**：不只校验模型生成的数字，连数据源本身的数字都校验。

### 5.5 容灾切换

主数据源挂了（比如 AKShare 限流），自动切下一个：

```
⚠️ AKShare 抓取失败，自动切换到新浪财经 HTTP
```

**配置**：`auto_failover: true`（默认开启）

---

## 六、每日自选股推送（最省心）

配一次之后，**每天自动收到飞书/企业微信通知**，不用打开任何页面。

### 6.1 第一步：配置推送渠道

在飞书/企业微信群里添加自定义机器人，拿到 webhook 地址，填到 `.env`：

```bash
# 飞书自定义机器人
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx
FEISHU_SECRET=你的签名密钥（可选）

# 企业微信机器人（二选一）
WECOM_WEBHOOK_KEY=你的webhook key

# Telegram（可选）
TELEGRAM_BOT_TOKEN=你的bot token
TELEGRAM_CHAT_ID=你的chat id
```

### 6.2 第二步：编辑自选股列表

打开 `watchlist.txt`，一行一个股票代码：

```
600519
000858
300750
601318
```

### 6.3 第三步：手动跑一次测试

```bash
python main.py daily
```

每只股票会自动生成速览并推送到群里。

### 6.4 第四步：GitHub Actions 定时自动跑（推荐）

1. 把代码推到你的 GitHub 仓库
2. 在仓库 Settings → Secrets 里添加：
   - `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`
   - `FEISHU_WEBHOOK_URL`、`FEISHU_SECRET`（或 WECOM_WEBHOOK_KEY）
3. 已经配好的 `.github/workflows/daily-report.yml` 会在**每个工作日早上 9 点（北京时间）**自动运行
4. 也可以在 Actions 页面手动触发运行

---

## 七、反幻觉统计

本项目的核心特色是**反幻觉**。每次跑完报告，系统会自动记录：
- 这份报告的幻觉率是多少
- 可信度等级（A/B/C/D）
- 用的哪个模型

### 7.1 查看历史统计

```bash
python main.py stats
```

输出示例：

```
📊 幻觉率历史统计（共 12 次）
平均幻觉率：4.2%

🏆 模型可信度排名（越低越好）：
  1. deepseek-chat：3.1%（跑了 8 次）
  2. gpt-4o：5.8%（跑了 4 次）
```

### 7.2 可信度等级说明

| 等级 | 幻觉率 | 含义 |
| --- | --- | --- |
| A | < 5% | 高可信，直接用 |
| B | 5-15% | 中可信，关键数据再核对一下 |
| C | 15-30% | 低可信，建议人工复核 |
| D | > 30% | 不可信，重跑吧 |

### 7.3 怎么用这个数据

1. **跑几份报告后看看**：跑 5-10 份不同公司的报告，然后用 `stats` 命令看看你的模型靠不靠谱
2. **对比不同模型**：换个模型跑几份，看看幻觉率有没有变化，哪个更靠谱
3. **高风险主题预警**：如果某类报告的幻觉率特别高（比如 > 15%），说明这类主题容易出幻觉，建议人工复核

---

## 八、常见问题

### Q：跑 `brief` 命令报错 ModuleNotFoundError？

A：装依赖：`pip install -r requirements.txt`。

### Q：想换模型厂商？

A：改 `.env` 里的 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 三行即可，代码无需改动。

### Q：Web 界面打不开？

A：确认装了 fastapi：`pip install fastapi uvicorn`，然后跑 `python main.py serve`。

### Q：数据抓不到 / 报错？

A：公开数据接口存在限流/偶发断连，本项目对每个接口做有限次重试，关键数据失败时自动回退到离线样例并显著标注，保证流程不中断。

### Q：报告里的数字对不对？

A：报告结尾有「🛡️ 反幻觉总览」章节，会告诉你：
- 数字校验了多少处
- 多少一致、多少矛盾
- 可信度等级是 A/B/C/D

如果可信度是 A（< 5% 幻觉率），可以放心用。

### Q：怎么导出 PDF？

A：加 `--pdf` 参数：`python main.py brief 600519 --pdf`。

### Q：MCP 接口怎么用？

A：启动 Web 服务后，`POST /mcp` 可被 Claude Desktop / Cursor 直接接入。`GET /mcp/tools` 浏览器直接查看暴露的所有工具。

---

## 九、配置文件详解

本项目有两个配置文件：
- `.env`：环境变量（密钥、token 等）
- `config.yaml`：功能配置（数据源、模型参数等）

### 9.1 .env 文件详解

```bash
# ===== 大模型配置（必填）=====
LLM_BASE_URL=https://api.deepseek.com/v1    # 大模型 API 地址
LLM_API_KEY=sk-你的key                       # 大模型 API 密钥
LLM_MODEL=deepseek-chat                      # 模型名称

# ===== 推送配置（可选）=====
# 飞书自定义机器人
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx
FEISHU_SECRET=你的签名密钥（可选）

# 企业微信机器人
WECOM_WEBHOOK_KEY=你的webhook key

# Telegram Bot
TELEGRAM_BOT_TOKEN=你的bot token
TELEGRAM_CHAT_ID=你的chat id

# ===== 付费数据源配置（可选）=====
# Tushare
TUSHARE_TOKEN=你的tushare token

# 聚宽 JQData
JQDATA_USERNAME=你的聚宽账号
JQDATA_PASSWORD=你的聚宽密码

# 米筐 RiceQuant
RQDATA_USERNAME=你的米筐账号
RQDATA_PASSWORD=你的米筐密码
```

### 9.2 config.yaml 文件详解

```yaml
# 大模型配置
llm:
  model: deepseek-chat        # 默认模型
  temperature: 0.35           # 温度（越低越稳定）
  max_tokens: 4000             # 最大输出 token 数

# 数据源配置
data_source:
  sources:                     # 启用的数据源列表
    - akshare
    - eastmoney_http
  auto_failover: true          # 主挂了自动切下一个
  cross_check_enabled: true    # 多源交叉验证
  cross_check_tolerance: 0.01  # 误差阈值（1%）

# 反幻觉配置
verify:
  enabled: true                # 开启数字校验
  tolerance: 0.01              # 数字容差（1%）
  semantic_check: true         # 开启语义层校验

# 推送配置
notify:
  enabled: false               # 默认关闭推送
  channels:                    # 启用的推送渠道
    - feishu
```

---

## 十、最佳实践

### 10.1 新手怎么用？

1. **先跑 demo**：`python main.py demo`，不用密钥，用样例数据走通流程
2. **配密钥**：去 `.env` 里填你的大模型 API
3. **跑第一个速览**：`python main.py brief 600519`
4. **打开 Web 界面**：`python main.py serve`，浏览器打开 http://localhost:8000

### 10.2 进阶用户怎么用？

1. **配多个数据源**：AKShare + Tushare 一起用，自动交叉验证
2. **配每日推送**：飞书/企微机器人 + GitHub Actions 定时任务
3. **跑 stats 看幻觉率**：跑 5-10 份报告后，看看你的模型靠不靠谱
4. **选可信度 A 级的报告**：如果可信度是 B/C/D，建议人工复核

### 10.3 怎么避免幻觉？

1. **选好模型**：不同模型幻觉率不一样，用 `stats` 命令对比
2. **多看反幻觉总览**：报告结尾有「🛡️ 反幻觉总览」章节
3. **多源交叉验证**：选多个数据源，同一个数字不一样就标记出来
4. **关键数据人工复核**：做投资决策前，关键数据自己再核对一下

---

## 十一、故障排查

### 11.1 跑 brief 命令报错 ModuleNotFoundError

**现象**：`ModuleNotFoundError: No module named 'akshare'`

**解决**：
```bash
pip install -r requirements.txt
```

### 11.2 数据抓不到 / 超时

**现象**：`⚠️ AKShare 抓取失败，自动切换到离线样例`

**原因**：公开数据接口限流/偶发断连

**解决**：
- 等一会儿再跑（限流通常几分钟就恢复）
- 或者配多个数据源（AKShare 挂了自动切新浪/腾讯）

### 11.3 大模型报错 / 超时

**现象**：`⚠️ 大模型调用失败`

**原因**：API 密钥不对 / 网络问题 / 模型服务器挂了

**解决**：
- 检查 `.env` 里的 `LLM_API_KEY` 对不对
- 检查网络能不能访问大模型 API
- 换个模型试试

### 11.4 Web 界面打不开

**现象**：浏览器打开 http://localhost:8000 打不开

**解决**：
- 确认装了 fastapi：`pip install fastapi uvicorn`
- 确认命令跑对了：`python main.py serve`
- 看看端口有没有被占用：换个端口 `python main.py serve --port 8001`

### 11.5 推送收不到

**现象**：跑 `--push` 参数，但飞书/企微没收到消息

**解决**：
- 检查 `.env` 里的 webhook URL 对不对
- 检查飞书/企微机器人有没有添加成功
- 看看终端输出有没有「推送成功」的日志

---

> 💡 **还有问题？** 提 Issue，或者看 README 了解更多功能。
