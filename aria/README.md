# ARIA — Adaptive Reasoning Intelligence Architecture

Cognitive AI trading intelligence layer with bidirectional Obsidian memory.

---

## Architecture

```
aria/
├── aria.py                  ← CLI entry point (run this)
├── requirements.txt
├── config/
│   └── .env.example         ← Copy to .env and fill in keys
├── core/
│   ├── session.py           ← Session manager + agentic loop
│   ├── system_prompt.py     ← Dynamic prompt builder with memory injection
│   └── display.py           ← Terminal display utilities
├── memory/
│   ├── working_memory.py    ← Session state + prompt injection
│   └── obsidian_bridge.py   ← Bidirectional Obsidian vault R/W
└── tools/
    ├── ticker.py            ← yfinance + technical indicators + signal score
    ├── contradictions.py    ← Cross-signal conflict detection
    ├── debate.py            ← Multi-agent Bull/Bear/Risk/Quant/Macro debate
    ├── scenario.py          ← Macro risk scenario analysis
    ├── options.py           ← Black-Scholes + Greeks calculator
    ├── news.py              ← NewsAPI + RSS + NLP sentiment
    ├── macro.py             ← FRED macro indicators + yield curve
    ├── political.py         ← Congressional trading data
    └── chart.py             ← Chart image encoder for vision API
```

---

## Setup

### 1. Install dependencies

```bash
cd aria
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp config/.env.example config/.env
```

Edit `config/.env`:

```
ANTHROPIC_API_KEY=your_key_here
OBSIDIAN_VAULT_PATH=C:\Users\sound\Documents\DigitalBrain
NEWSAPI_KEY=optional_but_recommended
FRED_API_KEY=optional_for_macro_data
```

**API keys:**
- Anthropic: https://console.anthropic.com
- NewsAPI (free tier): https://newsapi.org/register
- FRED (free): https://fred.stlouisfed.org/docs/api/api_key.html

### 3. Run ARIA

```bash
python aria.py
```

**Options:**
```bash
python aria.py --ticker NVDA          # start focused on a ticker
python aria.py --chart /path/chart.png # analyse chart on startup
python aria.py --no-memory            # ephemeral session (no vault)
python aria.py --debug                # show tool calls
python aria.py --vault /custom/path   # override vault path
```

---

## CLI Commands

| Command | Description |
|---|---|
| `/chart <path>` | Analyse a chart image (jpg/png) |
| `/debate` | Run 5-agent Bull/Bear/Risk/Quant/Macro debate |
| `/scenario` | Run macro risk scenario (rate shock, crash, etc.) |
| `/watchlist` | Show vault watchlist |
| `/memory` | Show working memory state |
| `/save` | Save session to Obsidian vault |
| `/clear` | Clear conversation (keep memory) |
| `/help` | Show command menu |
| `/exit` | Save and exit |

---

## Obsidian Vault Structure

ARIA creates this structure inside your vault:

```
ARIA/
├── memory/
│   ├── long-term-memory.md   ← permanent learnings (auto-updated)
│   └── recent-memory.md      ← 48h rolling context
├── sessions/                 ← per-session analysis notes
├── decisions/                ← per-ticker decision logs
├── signals/                  ← signal scores per ticker
├── debate_logs/              ← cognitive debate outputs
├── chart_reads/              ← chart analysis logs
├── macro/                    ← macro snapshots
└── watchlist/
    └── watchlist.md          ← your ticker watchlist
```

ARIA reads long-term and recent memory on startup, injects them into the system prompt, and writes analysis back after each significant response.

---

## ARIA's Cognitive Toolkit

| Tool | What it does |
|---|---|
| `get_ticker_data` | OHLCV, RSI, MACD, Bollinger, ATR, MA, composite signal score |
| `check_contradictions` | Cross-validates signals — surfaces conflicts before high-conviction views |
| `run_cognitive_debate` | 5-agent debate (Bull/Bear/Risk/Quant/Macro) with synthesis |
| `run_scenario` | Macro risk scenarios: rate shock, crash, vol spike, stagflation, etc. |
| `get_options_greeks` | Black-Scholes price + Delta/Gamma/Theta/Vega/Rho |
| `get_news_sentiment` | Headlines + NLP sentiment with negation detection |
| `get_macro_data` | Fed rate, CPI, unemployment, GDP, yield curve, DXY, VIX |
| `get_political_data` | Congressional trading activity |

---

## Example Session

```
[ARIA] > python aria.py

[You] What's the read on NVDA?

[ARIA] Let me pull the data and check for contradictions before I give you a view.

[tool: get_ticker_data] {"ticker": "NVDA"}
[tool: check_contradictions] {"ticker": "NVDA"}

[ARIA] NVDA is sitting at $891.20, up 1.4% today on above-average volume (1.6x 20-day avg) — 
that's a meaningful detail. The signal score comes in at 68.4, bullish direction...

[You] /debate

  Ticker to debate: NVDA

[ARIA] Running the full 5-agent debate on NVDA...
```

---

## Memory Flow

```
Startup:
  Obsidian vault → long-term-memory.md + recent-memory.md
  → WorkingMemory.load_from_vault()
  → Injected into system prompt as VAULT MEMORY section

During session:
  ARIA responses → WorkingMemory.update_from_aria_response()
  Significant analysis → ObsidianBridge.write_session_note() [auto]

On /save or /exit:
  WorkingMemory → ObsidianBridge.write_session_summary()
  New learnings → ObsidianBridge.promote_to_long_term()
  recent-memory.md → overwritten with current session state
```

---

## Notes

- All ARIA outputs are research signals only. Not trade execution recommendations.
- The cognitive debate uses Claude claude-sonnet-4-6 to simulate each analyst agent independently.
- Signal scores are composite technical scores (0-100). They are not ML predictions — they are structured summaries of technical indicators.
- Chart analysis uses Claude's vision capability — upload any screenshot from your charting platform.
