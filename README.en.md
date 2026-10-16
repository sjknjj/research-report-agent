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

> **No system can guarantee an error-free report.** The goal here is **"zero
> unlabelled uncertainty"** — surface what the system could not verify instead of
> handing you a blanket "pass".

Four defences, **fully active on the company-brief and announcement paths**
(those have a known target company, so a `FactBase` can be built):

1. **Input constraint** — the model only ever sees a cleaned "fact pack".
2. **Numeric cross-check** — **every** financial number in the output is classified:
   - `supported` / `mismatch` / `unverifiable` when the number maps to a known metric;
   - **`unsourced`** when it cannot be tied to the fact base at all — these are
     listed explicitly instead of being silently ignored.
   Tolerance is **tiered by metric importance**: core metrics (revenue, net profit,
   EPS, ROE, margins, YoY/QoQ growth) use 0.5% / 0.06pp, others 1% / 0.1pp.
   Thousands separators **and Chinese numerals** (`一百五十二点三亿元`,
   `百分之四十二点三`) are supported; facts are indexed by reporting period.
3. **Semantic faithfulness check** — an LLM flags claims not supported by the
   fact pack (online mode only).
4. **Logic review & citation grounding** — accounting-identity checks in three
   states (pass / partial when line items are missing / fail), **in-report
   contradiction detection**, and, for the deep report, **citation grounding**:
   every number in the body must appear in the source it cites.

### Three-state verdict (no more false sense of safety)

The old verdict only asked "is there a mismatch?", so a report whose numbers were
**all unsourced** still displayed "pass". Now:

| Verdict | Trigger |
|---|---|
| ✅ **pass** | No mismatch, coverage ≥60%, unsourced ≤30%, unverifiable ≤50%, no contradictions |
| ⚠️ **pass with caveats (human review required)** | No mismatch, but any of the above is not met — reasons are listed |
| ❌ **fail** | A number contradicts the fact base |

Reported alongside: **`coverage`**, the **unsourced list**, **internal
contradictions**, and **`confidence_reasons`**. Confidence grades
(A <5% / B 5–15% / C 15–30% / D >30%) are downgraded automatically by coverage,
unsourced ratio and contradictions.

> ⚠️ **Scope caveat:** the **deep report** (`report`) is topic-based research with
> no target company, so there is no `FactBase`. It uses **citation grounding**
> instead — numbers must be traceable to their cited sources. Its
> "🛡️ Anti-hallucination overview" states which checks actually ran and marks the
> rest as "not enabled"; when nothing ran, confidence is "unrated".

> ❌ **Out of scope** (state this openly): fabricated entities/dates/events
> (non-numeric claims), "right numbers but wrong conclusion" reasoning errors,
> the trustworthiness of the *sources* themselves, and errors in the fact base
> itself. "Unsourced" means *the system could not verify it* — not that it is wrong.

> ℹ️ Years, ticker codes and plain counts ("5 companies") are excluded from the
> coverage denominator.

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

Two transports:

- **stdio (recommended for Claude Desktop / Cursor)** — `python mcp_server.py`,
  configured as `{"command": "python", "args": ["/abs/path/mcp_server.py"]}` in
  `claude_desktop_config.json`.
- **HTTP** — `POST /mcp` is plain JSON-RPC over HTTP (no session/SSE), handy for
  curl and custom clients.

Implemented methods: `initialize`, `notifications/initialized` (no response),
`ping`, `tools/list`, `tools/call`.

Tools: `get_stock_brief(symbol)`, `verify_numbers(text, symbol)`,
`search_web(query, max_results)`, `generate_stock_report(topic, deep)`.

---

## Tests

```bash
python -m pytest tests/ -q      # 288 tests
```

All tests run offline — no network and no API key required.

---

## Known limitations

| Item | Status |
|---|---|
| Wind / Choice / iFinD / JQData / RiceQuant / Tushare | Require paid terminals/accounts — **cannot be verified in this repo**. Choice/iFinD adapters use non-official package names and likely need rewriting. |
| MCP data source | Protocol + code implemented with offline tests, **not yet tested against a real MCP server**. |
| Push channels | Signature/request construction verified, **not tested against real webhooks**. |
| GitHub Actions | Workflow fixed, **never executed on a real repo**. |
| Docker image | Dockerfile fixed (includes CJK fonts), **never built/verified here**. |
| Semantic check | Requires an online LLM; skipped offline. |
| RAG | Relevance threshold added, but **source truthfulness is not verified**. |

---

## Disclaimer

For learning and research only. Nothing here is investment advice. Generated
reports may still contain errors; always verify critical figures yourself.

## License

GNU AGPL v3.0
