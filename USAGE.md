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

### 4.1 公司速览

```bash
python main.py brief 600519                  # 贵州茅台速览
python main.py brief 600519 --pdf           # 顺便导出 PDF
python main.py brief 600519 --push          # 生成完自动推送到飞书/企微
```

### 4.2 深度研报

```bash
python main.py report 低空经济产业链分析      # 生成完整研报
python main.py report 低空经济产业链分析 --deep  # 开启递归深挖（多轮补检知识缺口）
```

### 4.3 多公司对比

```bash
python main.py compare 600519 000858 000568   # 对比贵州茅台、五粮液、泸州老窖
python main.py compare 600519 000858 --by roe # 按 ROE 排序
```

报告会自动生成：
- 📊 对比表格（PE/PB/增速/ROE/技术面）
- 🕸️ 雷达图（5维度打分）
- 📊 估值分位图（PE/PB 柱状图）

### 4.4 公告提炼

```bash
python main.py announce "这里粘贴公告文本"
```

自动做：
- 提炼公告要点
- 跟财报交叉校验数字
- 标记「公告说的数字跟财报对不对得上」

### 4.5 其他命令

```bash
python main.py history                    # 列出所有历史报告
python main.py history --keyword 600519   # 按关键词搜索
python main.py monitor                     # 盘中实时监控（达涨跌幅阈值自动推送）
python main.py monitor --threshold 3      # ±3% 就推送（默认 ±5%）
python main.py stats                       # 查看幻觉率历史统计
python main.py serve                       # 启动 Web 服务
```

---

## 五、多数据源配置

### 5.1 Web 界面选（最方便）

打开 Web 界面 → 公司速览页 → 勾选想用的数据源 → 生成速览。

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
```

### 5.3 付费数据源怎么配

在 `.env` 里加对应的 token / 账号：

```bash
# Tushare（注册就送积分，够用）
TUSHARE_TOKEN=你的tushare token

# 聚宽 JQData
JQDATA_USERNAME=你的聚宽账号
JQDATA_PASSWORD=你的聚宽密码

# 米筐 RiceQuant
RQDATA_USERNAME=你的米筐账号
RQDATA_PASSWORD=你的米筐密码

# Wind / Choice / iFinD
# 不用配，装了对应终端就能用
```

### 5.4 多源交叉验证效果

选多个数据源后，系统会自动比对同一个数字：

```
⚠️ 数据不一致：市盈率（数据源1=25.3，数据源2=26.1）最大最小误差 3.1%
```

**这就是反幻觉的终极形态**：不只校验模型生成的数字，连数据源本身的数字都校验。

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

> 💡 **还有问题？** 提 Issue，或者看 README 了解更多功能。
