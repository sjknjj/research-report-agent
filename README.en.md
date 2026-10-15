# Research Report Agent

An A-share (China) equity research assistant built on LLMs + AKShare, with a
deterministic **anti-hallucination** layer that cross-checks numbers instead of
trusting the model.

> 📖 Full step-by-step tutorial (Chinese): [USAGE.md](./USAGE.md)

---

## What it does

| Capability | Description |
|---|---|
| **Company brief** | Enter a stock code → quote, financials, technicals, cross-verification, PDF export |
| **Deep report** | Enter a topic → multi-agent pipeline (plan → search → analyse → debate → write → review) |
| **Multi-company compare** | Several codes → comparison table + radar chart + valuation-percentile chart |
| **Announcement extraction** | Paste an announcement → key points, numbers cross-checked against financials |
| **Industry analysis** | Enter an industry → constituent stocks + sector aggregates |
| **Sentiment / backtest / factors** | News sentiment, MACD backtest, 4-factor scoring |

Also included: Feishu / WeCom / Telegram push, a daily watchlist job via GitHub
Actions, an MCP server, RAG retrieval, and a FastAPI web UI (13 tabs).

---

## Anti-hallucination: what it actually does

Four defences, **fully active on the company-brief and announcement paths**
(those have a known target company, so a `FactBase` can be built):

1. **Input constraint** — the model only ever sees a cleaned "fact pack".
2. **Numeric cross-check** — numbers in the output are matched against the fact
   base (1% tolerance for amounts, 0.1pp for percentages). Year-over-year values
   keep their sign; thousands separators are supported.
3. **Semantic faithfulness check** — an LLM flags claims not supported by the
   fact pack (online mode only).
4. **Logic review** — citation coverage, accounting-identity checks
   (assets = liabilities + equity, etc.).

> ⚠️ **Scope caveat:** the **deep report** (`report`) is topic-based research with
> no target company, so there is no `FactBase` and **no numeric cross-check**.
> Its "🛡️ Anti-hallucination overview" section states which checks actually ran
> and marks the rest as "not enabled"; confidence is reported as "unrated".
> Use `brief` / `announce` when you need number-level verification.

Verification has limits we prefer to state openly: only numbers matching the
built-in metric dictionary (~16 metrics, plus deducted-net-profit) are checked;
other figures are simply not inspected.

---

## Quick start (3 minutes)

```bash
git clone https://github.com/sjknjj/research-report-agent.git
cd research-report-agent

pip install -r requirements.txt

cp .env.example .env      # then fill in your LLM_API_KEY

# Offline demo — no API key, no network needed
python main.py demo

# Online
python main.py brief 600519
```

### Docker

```bash
cp .env.example .env      # fill in your key
docker-compose up -d
# open http://localhost:8000
```

The image installs CJK fonts so PDF export renders Chinese correctly.

---

## Web UI

```bash
python main.py serve              # default port 8000
python main.py serve --port 8080
```

Open <http://localhost:8000>. 13 tabs: Home, Compare, Industry, Deep report,
Announcement, Sentiment, Backtest, Factors, Stats, Watchlist, Daily job, API key
guide, Settings.

Notes:
- "Auto refresh" polls the lightweight `/api/quote` endpoint every 10s — it does
  **not** re-run the LLM pipeline.
- "Settings" persists push channels and alert thresholds to
  `output/web_settings.json`.

---

## CLI

| Command | Purpose |
|---|---|
| `python main.py brief 600519` | Company brief |
| `python main.py report 低空经济` | Deep report |
| `python main.py compare 600519 000858` | Multi-company comparison |
| `python main.py industry 银行` | Industry analysis |
| `python main.py announce --file notice.txt --symbol 600519` | Announcement extraction |
| `python main.py daily` | Watchlist batch + push |
| `python main.py monitor` | Intraday alerting |
| `python main.py history` | List generated reports |
| `python main.py stats` | Hallucination-rate statistics |
| `python main.py demo` | Offline end-to-end demo |

Add `--pdf` to also export a PDF, `--sample` to force offline sample data.

---

## Configuration

- **`.env`** — API keys and runtime options (see `.env.example`).
- **`config.json`** — optional structured config (copy `config.example.json`).
  YAML is **not** supported.

Any OpenAI-compatible endpoint works: DeepSeek (default), OpenAI, DashScope
(Qwen), Zhipu, Moonshot, or a local Ollama server.

---

## MCP integration

Start the web service and point an MCP client at `http://localhost:8000/mcp`
(Streamable HTTP). Implemented methods: `initialize`,
`notifications/initialized`, `ping`, `tools/list`, `tools/call`.

Tools: `get_stock_brief(symbol)`, `verify_numbers(text, symbol)`,
`generate_stock_report(topic, deep)`.

---

## Tests

```bash
python -m pytest tests/ -q
```

All tests run offline — no network and no API key required.

---

## Disclaimer

For learning and research only. Nothing here is investment advice. Generated
reports may still contain errors; always verify critical figures yourself.

## License

GNU AGPL v3.0
