# 使用教程 · 从安装到看懂「反幻觉结论」

**零基础也能跟着做。每一步都有命令和预期结果。**

> 想看项目全貌与机制原理？见 [README.md](./README.md)

---

## ⚡ 最快上手（30 秒）

```bash
git clone https://github.com/sjknjj/research-report-agent.git
cd research-report-agent
pip install -r requirements.txt
cp .env.example .env          # 填入你的 LLM_API_KEY（见第四节）
python main.py serve          # 打开 http://localhost:8000
```

**不想配 key 也行**：`python main.py demo` 用离线样例数据跑通全流程（不联网）。

---

## 📋 目录

1. [准备工作](#一准备工作)
2. [安装方式一：Docker](#二安装方式一docker一键部署)
3. [安装方式二：本地 Python](#三安装方式二本地-python)
4. [配置大模型 API Key](#四配置大模型-api-key)
5. [启动 Web 界面](#五启动-web-界面)
6. [13 个 Tab 逐个讲](#六13-个-tab-逐个讲)
7. [**怎么读懂「交叉校验结论」**](#七怎么读懂交叉校验结论)
8. [命令行怎么用](#八命令行怎么用)
9. [配每日自动推送](#九配每日自动推送)
10. [常见问题](#十常见问题)

---

## 一、准备工作

| 东西 | 要不要 | 说明 |
|---|---|---|
| 一台电脑 | ✅ 必须 | Mac / Windows / Linux |
| 大模型 API Key | ✅ 必须（除离线演示外） | 推荐 DeepSeek，便宜 |
| Python 3.10+ | 🔶 二选一 | 不用 Docker 才需要 |
| Docker | 🔶 二选一 | 省去装 Python 与依赖 |
| 飞书/企微/Telegram 机器人 | ❌ 可选 | 想自动推送才需要 |

---

## 二、安装方式一：Docker（一键部署）

### 2.1 装 Docker

下载并安装 <https://www.docker.com/products/docker-desktop/>，确保 Docker Desktop 在运行。

### 2.2 下载代码

```bash
git clone https://github.com/sjknjj/research-report-agent.git
cd research-report-agent
```

### 2.3 配 Key

```bash
cp .env.example .env
# 编辑 .env，把 LLM_API_KEY=sk-你的key填在这里 改成真实 key
```

### 2.4 启动

```bash
docker-compose up -d
```

浏览器打开 **http://localhost:8000**。

> 镜像已内置中文字体（`fonts-noto-cjk`），导出的 PDF 中文能正常显示。

---

## 三、安装方式二：本地 Python

```bash
# 1. 装依赖（建议用虚拟环境）
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. 配 key
cp .env.example .env

# 3. 先验证离线流程
python main.py demo

# 4. 启动 Web
python main.py serve
```

---

## 四、配置大模型 API Key

### 4.1 推荐 DeepSeek

1. 打开 <https://platform.deepseek.com/> 注册；
2. 左侧「充值」充 10 元（够用很久）；
3. 左侧「API Keys」→ 创建 → 复制 `sk-` 开头的串。

### 4.2 也可以换别家（任何 OpenAI 兼容端点）

| 模型 | Base URL | 模型名 |
|---|---|---|
| DeepSeek（默认） | `https://api.deepseek.com/v1` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-32k` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` |

编辑 `.env`：

```bash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-你的key
```

> 国内建议用 DeepSeek / 通义千问，OpenAI 需自行解决网络问题。

---

## 五、启动 Web 界面

```bash
python main.py serve              # 默认 8000
python main.py serve --port 8080  # 端口被占用时换一个
```

打开 **http://localhost:8000**，共 13 个 Tab，所有功能都能在网页上完成。

---

## 六、13 个 Tab 逐个讲

### 🏠 1. 首页（公司投研速览）

1. 输入股票代码（如 `600519` 贵州茅台）；
2. 「数据源选择」：默认勾 AKShare；**勾多个会启用容灾切换 + 财务口径交叉验证**
   （不勾也已经按 `akshare → sina_http → tencent_http` 自动容灾）；
3. 点「生成速览」。

**报告内容**：速览仪表盘、行情与财务、技术面信号、**交叉校验报告**、
三大报表勾稽、产业链图谱、多空辩论；导出的 HTML 里还有 K 线 / 财务趋势 / PE-PB 仪表盘。

**操作按钮**
- 📄 **导出 PDF**：下载 PDF；
- ⭐ **加入自选**：加入自选股；
- 🔄 **自动刷新**：每 10 秒刷新**实时行情**（只调轻量行情接口，不会重复调用大模型）；
  涨跌幅超过「设置」里配的阈值时，行情行会变成**琥珀色预警**。

---

### 📊 2. 多公司对比

1. 输入多个代码，逗号分隔（如 `600519,000858,000568`）；
2. 点「开始对比」。

输出：对比表（PE/PB/营收同比/净利同比/毛利率/ROE/区间涨跌/技术面）+ **雷达图** + **估值分位图**。
数据缺失显示 `-`，且**排序时缺失值永远排在最后**（不会出现"抓取失败的公司排第一"）。

---

### 🏭 3. 行业分析

输入行业名（如 `银行`、`半导体`），自动抓取成分股并聚合（家数、上涨家数、平均/中位数涨跌幅、领涨股）。
接口限流或离线时回退到 `data/samples/sample_industry_raw.csv` 的样例数据（会明确标注）。

---

### 📝 4. 深度研报

1. 输入主题（如 `低空经济产业链分析`）；
2. 可选：勾选「递归深挖检索」（**离线模式下无效**，会给出提示）；
3. 可选：选择搜索引擎（百度/必应/DuckDuckGo/Tavily，多选按优先级逐级回退）；
4. 点「生成研报」，进度会**实时**显示 6 个阶段。

> ⚠️ 深度研报是**主题研究**，没有标的公司事实库，因此做的是**引用溯源校验**：
> 正文里的数字必须能在它引用的检索资料里找到。
> 抓到网页正文时自动执行；只有标题/链接时（如离线演示）会如实显示「未执行」。

---

### 📢 5. 公告提炼

1. 粘贴公告文本；
2. 可选：填关联股票代码（会与财报交叉校验数字）；
3. 点「提炼要点」。

---

### 😊 6. 情绪面

输入代码 → 输出整体情绪、评分、正面/负面/中性条数与占比。
若模型未给出结构化结果（如离线模式），会明确写"未获得模型分析结果"，而不是假装中性。

---

### 📈 7. 回测

输入代码 → 回测 MACD 金叉/死叉策略。结果包含：
完成交易次数、盈利/亏损次数、胜率、平均单笔收益、**策略累计收益**、**同期买入持有**、最大回撤。
策略已计入**佣金与印花税**，信号**次日成交**（避免前视），期末未平仓会强制平仓。

---

### ⭐ 8. 因子

输入代码 → 估值/成长/盈利/动量 4 个维度各打 0–10 分 + 综合分。
**缺失维度会从权重中剔除并注明**，不会用"看起来合理"的默认值伪造分数；
亏损公司（PE≤0）不会被算成"最便宜"。

---

### 📊 9. 统计

查看历史幻觉率、模型可信度排名（按声明条数加权）、最近若干次校验记录。
数据来自 `output/.stats.json`。

---

### ⭐ 10. 自选股

查看已收藏股票，点击可快速生成速览，点 `×` 可删除（保存在浏览器 localStorage）。

---

### 📅 11. 每日跑批（教程）

页面内有图文步骤：Fork 仓库 → 在 Settings → Secrets 配 `LLM_API_KEY` 等 →
按需编辑 `watchlist.txt` → 每个工作日 09:00（北京时间）自动跑并推送到飞书/企微。

---

### 🔑 12. API 配置（教程）

页面内有各厂商 API Key 的申请步骤。

---

### ⚙️ 13. 设置

1. 填飞书 Webhook / 企业微信 Webhook Key；
2. 设涨幅、跌幅预警阈值；
3. 点「保存设置」。

保存到 `output/web_settings.json`，**立即生效**（推送渠道无需重启）。页面重开会自动回填（密钥脱敏）。

**阈值确实会被使用**：
- 首页「自动刷新」时超阈值 → 行情行琥珀色预警；
- `python main.py monitor` 不传 `--threshold` 时读取这里的阈值。

---

## 七、怎么读懂「交叉校验结论」

这是本项目最重要的部分，**请务必花 2 分钟看完**。

### 7.1 结论有三种，不要只看"通过"

| 结论 | 含义 | 你该怎么做 |
|---|---|---|
| ✅ **通过** | 没有矛盾，且覆盖率 ≥60%、无来源 ≤30% | 正常使用，关键数据仍建议扫一眼 |
| ⚠️ **通过但有保留（需人工复核）** | **没有发现矛盾**，但覆盖率太低 / 无来源太多 / 有内部矛盾 | **不能当成"通过"**——按报告列出的原因逐条复核 |
| ❌ **未通过** | 存在与事实库矛盾的数字 | 看「自动纠错记录」，或重新生成 |

### 7.2 报告里的几组数字

```
结论：⚠️ 通过但有保留（需人工复核）
可信度：C（低可信）
共核对 16 个数字 —— 一致 14、矛盾 0、待核 0、无来源 2；可核实覆盖率 88%；疑似幻觉率 0.0%
```

| 名词 | 含义 |
|---|---|
| **一致 / 矛盾 / 待核** | 命中指标词典的数字：与事实库一致 / 矛盾 / 事实库没这项数据 |
| **未指明指标但已核实** | 数值在事实库里对得上，只是正文没写它对应哪个指标（计入覆盖率，**不算**无来源） |
| **无来源** | **没被词典归因、事实库里也找不到对应值**的数字 → 报告会列出清单 |
| **可核实覆盖率** | （一致 + 未指明指标但已核实）÷ 全部财务数字；越低说明越多数字没能核实 |
| **疑似幻觉率** | 矛盾数字 ÷ 已归因数字 |

### 7.3 「无来源」不等于幻觉

它表示**系统核实不了**：可能确实来自公告或调研，也可能是编的。所以：
**看到"无来源"就必须人工复核**，这也是系统把它单独列出来的原因。

### 7.4 哪些数字会被检查

- 命中指标词典的 **25 项**（营收/净利/扣非净利/同比/环比/毛利率/净利率/ROE/EPS/股价/
  涨跌幅/波动率/换手率/区间高低价/**5 日与 20 日均线**/PE/PB/股息率/主力净流入…），
  其中 **12 项核心指标**；
- 核心指标容差 **0.5% / 0.06 个百分点**，其余 **1% / 0.1 个百分点**；
- 支持千分位（`1,234.56亿元`）与中文数字（`一百五十二点三亿元`、`百分之四十二点三`）；
- 支持 **Markdown 加粗**写法（`销售毛利率 **29.99%**`）——模型写报告几乎都是这个格式；
- 支持**表格**：按表头绑定指标、按首列绑定报告期，连表格上方的 `（单位：亿元）` 也会读进来，
  所以「近五期趋势」这类表格数字**会被真正核对**；
- 容差会按**数字写出来的精度**补偿：`0.09`（亿元）本身只有 ±0.005 亿的精度，
  不会因为"位数少"被判成矛盾；
- 年份、股票代码、"5 家"这类纯计数**不计入**覆盖率分母。

### 7.5 这套校验**管不到**什么（重要）

1. 编造的公司名、日期、事件、客户、订单等**非数字内容**；
2. 「数字都对，但结论错了」的**推理错误**；
3. **来源本身的真伪**（只校验"数字 ↔ 事实库/引用来源"是否一致）；
4. **事实库本身出错**时，会把错误值当成"事实"。

> 一句话：**没有任何系统能确保报告不出错**。本项目能做到的是
> "**把系统核实不了的部分明确标出来**"，而不是给你一个笼统的"通过"。

---

## 八、命令行怎么用

| 命令 | 作用 |
|---|---|
| `python main.py brief 600519` | 公司速览 |
| `python main.py report 低空经济` | 深度研报 |
| `python main.py compare 600519 000858` | 多公司对比（逗号分隔也支持） |
| `python main.py industry 银行` | 行业分析 |
| `python main.py announce --file notice.txt --symbol 600519` | 公告提炼 |
| `python main.py daily` | 自选股批量跑 + 推送 |
| `python main.py monitor` | 盘中监控（阈值缺省读设置页） |
| `python main.py history --keyword 600519` | 历史报告 |
| `python main.py stats` | 幻觉率统计 |
| `python main.py demo` | 离线演示 |
| `python main.py serve --port 8080` | 启动 Web |

**常用参数**

```bash
python main.py brief 600519 --pdf        # 额外导出 PDF
python main.py brief 000000 --sample     # 强制离线样例（无需 key）
python main.py report 低空经济 --deep     # 递归深挖检索
python main.py report 低空经济 --mode offline --provider mock   # 完全离线跑通
python main.py brief 600519 --md-only    # 只导出 Markdown
```

---

## 九、配每日自动推送

### 9.1 拿到 webhook

- **飞书群机器人**：群设置 → 机器人 → 添加自定义机器人 → 复制 webhook URL；
- **企业微信群机器人**：群设置 → 添加机器人 → Webhook 地址里的 key；
- **Telegram**：找 `@BotFather` 创建 bot 拿 token，给 bot 发消息后 `getUpdates` 拿 chat_id。

### 9.2 填到 `.env`

```bash
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx
FEISHU_SECRET=                          # 若机器人开了签名校验
WECOM_WEBHOOK_KEY=
TELEGRAM_BOT_TOKEN= / TELEGRAM_CHAT_ID=
```

### 9.3 本地测试

编辑 `watchlist.txt`（一行一个代码），然后：

```bash
python main.py daily
```

### 9.4 交给 GitHub Actions

- Fork 仓库 → Settings → Secrets and variables → Actions，添加
  `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`FEISHU_WEBHOOK_URL` 等；
- 工作流 `.github/workflows/daily-report.yml` 已配好：**工作日 09:00（北京时间）**；
- 缺少必需 Secret 时任务会**直接失败并提示**，不会拿空模型名去跑；
- 也可以在 Actions 页面手动触发。

---

## 十、常见问题

### 10.1 打不开网页？

1. 服务没启动 → `python main.py serve` 或 `docker-compose up -d`；
2. 端口被占 → `python main.py serve --port 8080`；
3. 代码不是最新 → `git pull`。

### 10.2 生成不了研报、报错？

1. 没配 Key → 编辑 `.env` 的 `LLM_API_KEY`；
2. Key 写错 → 检查是否复制完整；
3. 没余额 → DeepSeek 需充值；
4. 网络 → 国内用 DeepSeek / 通义千问，别用 OpenAI；
5. **想先排除环境问题** → `python main.py demo`（离线，不需要 key）。

### 10.3 搜索不到资料？

1. DuckDuckGo 国内被墙 → 换成百度/必应；
2. 用 Tavily 需要 key → <https://tavily.com/>；
3. 离线模式用的是 mock 检索（占位），不会真的搜。

### 10.4 结论显示「通过但有保留」是什么意思？

**不是出错，而是系统在如实告诉你"有些数字我核实不了"。** 到报告底部看
「交叉校验报告」里的**无来源数字清单**与**可信度下调原因**，逐条人工确认。

### 10.5 怎么提高覆盖率？

**不用改配置**——默认就按免费源完备度顺序启用了三个源（`akshare → sina_http → tencent_http`），
并且每个源内部还会自己换接口。想显式写死顺序就这样配：

```json
{ "data_sources": ["akshare", "sina_http", "tencent_http"],
  "auto_failover": true, "cross_check_enabled": true,
  "bypass_system_proxy": true }
```

系统会做**容灾切换**并对财务指标做**交叉验证**，一致的数字会变多、覆盖率上升。

### 10.5.1 为什么会看到 `ProxyError` / 「东财接口重试 3 次仍失败」？

这条日志特别容易误导人，拆开看：

| 日志片段 | 真实含义 |
|---|---|
| `ProxyError('Unable to connect to proxy', ...)` | **不是代理坏了**。连接走了代理隧道，隧道另一头（东财）断了，urllib3 分不清是谁断的，就记到代理头上 |
| `RemoteDisconnected` | 对方**一个字节都没回**就直接断开，典型的 IP 限流/风控 |
| `stock_zh_a_hist` | akshare 里这个函数**就是东财**（文件名 `stock_hist_em.py`，`_em` = East Money） |

所以「我选的是 akshare 啊」这句话有个前提误解：**akshare 是抓取库，不是数据源**。
它内部封装了东财/新浪/腾讯/同花顺，具体抓谁由函数决定。

现在的行为：东财日线挂了 → 自动换新浪 → 再换腾讯，**报告照常用真实行情生成**，
不会掉到离线样例。想确认降级有没有发生，看日志里的 `接口降级：... 换下一个接口`。

如果**所有**免费源都不通（比如整机断网），日志会明确写「所有候选接口均失败」+ 每个源的原因，
然后回退离线样例，并把 `source` 如实标注为 `sample`——**这时报告里的数字是虚构演示数据**。

### 10.5.2 `bypass_system_proxy` 是干嘛的？

默认 `true`，把 `push2his.eastmoney.com` 这类国内域名写进 `no_proxy` 让请求直连。

原因：macOS 上 `requests` 会通过系统设置自动读取代理，**即使你一个 `HTTP_PROXY` 环境变量都没有**。
Clash 这类工具转发国内行情接口时经常被对方判成风控直接断开，排查时却只看到 `ProxyError`。

只有"必须靠代理才能出网"的环境（比如海外网络）才需要设 `BYPASS_SYSTEM_PROXY=false`。

### 10.6 报告里出现「无来源数字」怎么办？

那是**词典覆盖不到、事实库里也没有对应值**的数字（比如研发投入、市占率、合同金额）。
必须人工核对：**"无来源"不等于它错，但系统无法为你担保**。

报告底部还会单列一类 **「未指明指标、但数值与事实库一致」**：数字本身查得到，
只是模型在正文里没写它对应哪个指标（典型是趋势表格里那一列列数字）。
这类**不算**无来源，也不拉低覆盖率，但建议扫一眼确认表头没串行。

> 百分比不享受这个待遇：`3.70%` 这种两位小数太容易撞上别的指标（毛利率/ROE 等），
> 纯靠数值相等就说"已核实"会放过真实的指标错配，所以仍按无来源处理。

### 10.7 深度研报为什么显示「数字校验未启用」？

因为它是**主题研究**，没有"标的公司"就没有事实库可对。
它走的是另一条防线——**引用溯源校验**（数字必须能在引用的资料里找到）。
所以在深度研报路径下可信度显示「未评级」，这是如实说明，不是故障。

### 10.8 报告生成太慢？

深度研报要跑 6 个阶段（规划 → 检索 → 逐章分析 → 多空辩论 → 撰写 → 审校），
一般 3–5 分钟。想快一点：用 DeepSeek、不勾选「递归深挖」、主题写具体一点。

### 10.9 用了「自动刷新」会不会很贵？

不会。自动刷新只轮询 `/api/quote` 实时行情接口，**不会**重新调用大模型。

### 10.10 数据不对/接口报错？

1. 看日志（默认输出到 stderr）里的具体错误；
2. 搜日志里的 `接口降级`：说明系统已经自动换了上游，报告数据仍然可信；
3. 看到「回退到离线样例」+ `source=sample` 才需要警惕——**那是虚构演示数据**；
4. 想换顺序/加付费源，配 `data_sources`（见 10.5）；
5. 已知限制见 README「已知限制」：付费数据源需要你自己的授权才能验证。

### 10.11 点「生成」提示「调用失败」/ `latin-1 codec can't encode`？

**十有八九是 `.env` 里的 `LLM_API_KEY` 没改。** 最常见的一幕：

```bash
# .env
LLM_API_KEY=sk-请替换为你的密钥      # ← 直接照抄了 .env.example
```

`LLM_MODE=auto` 原来只看 Key **非空**就判定"在线"，于是拿这串中文去发请求，
而 HTTP 头部只支持 latin-1，在 `http.client.putheader` 就抛
`UnicodeEncodeError: 'latin-1' codec can't encode characters`，
接口层再包成一句看不出原因的「调用失败」。

**现在的行为**：

- `auto` 模式只认**能用的** Key。占位符（`sk-请替换为…`、`your_api_key`、`sk-changeme`）
  和含中文等非 ASCII 字符的 Key，一律按"没有 Key"处理 → **自动走离线演示**，界面能正常出报告。
- 显式写 `LLM_MODE=online` 时不会静默降级，而是直接报一句能看懂的错：
  「LLM_API_KEY 不可用：它要么还是 .env.example 里的占位符，要么含中文等非 ASCII 字符……」。

**怎么修**：二选一

```bash
# 1) 填真实 Key（推荐，报告质量差别很大）
LLM_API_KEY=sk-你的真实key            # 不能有中文、空格、引号

# 2) 暂时用离线演示（有网络也无所谓，只用 mock）
LLM_MODE=offline
```

> 顺带一提：换成真实 Key 后记得**重启服务**，`load_settings` 是进程内单例，
> 改 `.env` 不会热生效。

---

## 🎉 好了，你现在会用也会看了

**最简流程**

1. `pip install -r requirements.txt`（或 `docker-compose up -d`）
2. `.env` 填 `LLM_API_KEY`
3. `python main.py serve` → 打开 <http://localhost:8000>
4. 首页输入股票代码 → 生成速览
5. **重点看「交叉校验报告」的结论与无来源清单**——这比报告本身更值得读
