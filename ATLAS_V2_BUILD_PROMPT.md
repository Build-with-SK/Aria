# ATLAS v2 — THE DISTRIBUTED MIND
## Complete Build Prompt (supersedes the sovereignty-first v1 constraints)

> v1 proved the cognitive architecture works: identity, memory palace, senses,
> proposals, synapse peering with ARIA. v2 keeps that soul and fixes what v1
> got wrong: intelligence ceiling, laptop-bound compute, UI, access, and
> always-on operation. The human's laptop GPU must end up FREE.

---

## THE FOUR TRUTHS THIS DESIGN ACCEPTS

1. **Intelligence comes from the model, not the architecture.** A 7B GGUF will
   never feel like Jarvis. v2 makes the language organ a ROUTER over multiple
   brains and always uses the best one available.
2. **Always-on requires a home.** University machines cannot host daemons.
   The nerve centre runs on cheap always-on compute (~$6-12/mo VPS or spare
   hardware at home), NOT the laptop.
3. **Multi-device access means security first.** Nothing gets exposed to the
   internet. Private mesh VPN only.
4. **The human is still the judgment layer.** Approval gates survive v2
   untouched. Alerts inform; they never act.

---

## TOPOLOGY

```
                    ┌─────────────────────────────────────────┐
                    │   NERVE CENTRE (always-on, ~$6-12/mo    │
                    │   CPU VPS or spare box at home, Docker) │
                    │                                         │
                    │  ATLAS soul: identity ▸ memory palace   │
                    │  ▸ senses ▸ reasoner ▸ proposals ▸      │
                    │  journal ▸ alert engine ▸ AGORA bus     │
                    │                                         │
                    │  ARIA: signal pipeline + brain daemon   │
                    │  (CPU is enough for both souls)         │
                    └───────┬──────────────┬──────────────────┘
                            │              │
              CORTEX ROUTER │              │ TAILSCALE MESH (free, private)
              (picks a brain│              │
               per thought) │              ├── Laptop (GPU now FREE — gaming,
                            │              │   music production, dev)
        ┌───────────────────┼──────────┐   ├── Phone (PWA + ntfy push alerts)
        │                   │          │   └── Any future device
        ▼                   ▼          ▼
  YOUR TRAINED MODEL   FRONTIER API   TINY CPU MODEL
  (vLLM on uni GPU     (Claude — for  (3B Q4 on the VPS —
   when up, or rented   deep/complex   routing, tagging,
   GPU by the hour)     thoughts)      routine noise-filter)
```

**Everything in v1's `src/mind/` survives.** The migration is: containerise,
swap the organ for the router, add the alert engine and Agora bus, replace the
UI, put Tailscale in front.

---

## MODULE 1 — CORTEX ROUTER (replaces the single organ)

### `atlas/src/mind/cortex.py`

Same interface as v1's `LanguageOrgan.think()` so nothing upstream changes.
Routes each thought by stakes, not by vendor loyalty:

```python
class Cortex:
    """
    Tiers (data/mind/cortex_config.json, hot-reloadable):
      reflex : tiny local CPU model (llama-cpp, ~3B Q4) — CARE noise filter,
               tagging, formatting. Free, instant, always available.
      core   : YOUR fine-tuned model behind an OpenAI-compatible endpoint
               (vLLM at university / rented GPU / laptop *only if the human
               explicitly powers it on as a donor*). ATLAS's own voice.
      apex   : frontier API (Claude) — DECIDE steps, weekly reflection,
               drafts the human will read, anything ambiguous or high-stakes.
               Only if api_key present; spend capped per day in config.

    think(prompt, stakes="routine"|"standard"|"high") -> str
      routine  → reflex
      standard → core, else apex, else reflex
      high     → apex, else core, else reflex
    Every call logs {tier_used, latency, tokens, cost} to cortex_ledger.jsonl.
    Health-checks core/apex every 5 min; degrades silently, never crashes.
    """
```

Config:
```json
{
  "reflex": {"gguf": "models/qwen2.5-3b-q4.gguf", "n_ctx": 4096},
  "core":   {"base_url": "http://uni-box.tailnet:8001/v1", "model": "atlas-v2", "api_key": "local"},
  "apex":   {"provider": "anthropic", "model": "claude-sonnet-latest",
             "daily_usd_cap": 2.00, "api_key_env": "ANTHROPIC_API_KEY"},
  "privacy": {"apex_may_see_vault": true, "apex_may_see_journal": false}
}
```

The `privacy` block is the new sovereignty: the human chooses per-source what
may ever leave the nerve centre. Journal defaults to NEVER.

Map v1 reasoner steps → stakes: SITUATE/CARE=routine, RECALL/THINK=standard,
PROPOSE/REFLECT + all drafts + weekly reflection=high.

---

## MODULE 2 — THE AGORA (multi-agent bus, generalises the synapse)

### `atlas/src/agora/`

v1's two hard-coded JSONL files become a registry-based bus so future agents
drop in without code changes:

```
agora/
  registry.json      [{name, role, endpoint, heartbeat_at, capabilities}]
  channels/
    market.jsonl     ARIA publishes: regime shifts, breakouts, cycle lessons
    life.jsonl       ATLAS publishes: schedule, human-state, priorities
    alerts.jsonl     anything any agent wants pushed to the human (see Module 3)
    tasks.jsonl      agent-to-agent requests: {from, to, ask, reply_to, status}
```

Message envelope (every line, every channel):
`{id, from, to|"*", at, kind: event|lesson|question|answer|alert, text, data, refs}`

Rules baked into a shared `agora.py` client library BOTH minds import:
- Agents subscribe by cursor per channel (v1's cursor pattern, proven).
- Every ingested message keeps provenance; each mind decides what to believe.
- No agent may command another. `tasks.jsonl` asks; the receiver may decline.
- New agent = register + subscribe. Nothing else changes. (Future: MUSE for
  music, FORGE for the freelance business — the human names them.)

ARIA-side change (additive, ~30 lines): publish to `market.jsonl` instead of
`aria_to_atlas.jsonl`; publish an `alert` on breakout conditions (Module 3).

---

## MODULE 3 — ALERT ENGINE (works with every UI closed)

### `atlas/src/mind/alerts.py` + push via ntfy

The answer to "it should alert me even when nothing is open": daemons run 24/7
on the nerve centre; pushes arrive on the phone via **ntfy** (free app; topic
subscription; self-hostable later if wanted).

```python
class AlertEngine:
    """
    Consumes agora/channels/alerts.jsonl. For each alert:
      severity: INFO   → appears in UI + morning briefing only
                NOTICE → ntfy push, batched (max 1 bundle / 2h)
                URGENT → ntfy push immediately, bypasses batching
    Dedupes (same key ≤ 24h = once). Quiet hours 23:00-07:30 hold
    everything except URGENT. Every push logged to alert_ledger.jsonl.
    """
```

**ARIA's breakout detector** (new, `src/brain/cognitive/alerter.py`, additive):
after each PERCEIVE, fire alerts for —
- composite score crosses ±40 with High confidence (NOTICE)
- price crosses a signal's stop_loss / take_profit / invalidation (URGENT)
- macro regime change (URGENT), VIX > 25 crossing (NOTICE)
- 52-week high/low break on a focus ticker (NOTICE)
Each: `{key: "breakout:AAPL:2026-07-08", severity, text, data:{ticker, score, price}}`

**ATLAS's important-things detector**: pending proposal > 24h old (NOTICE),
machine warnings from senses (NOTICE), anything its CARE step flags as urgent
for the human's day (severity chosen by a reflex-tier thought).

Push: `POST https://ntfy.sh/<long-random-topic>` — topic string lives in
config, treated as a secret. Phone subscribes once. Done.

---

## MODULE 4 — JARVIS UI (replaces the v1 tab page)

### `atlas/frontend/` rebuild — one dark HUD, PWA, voice

Aesthetic: Iron-Man HUD. Near-black (#04070d), a single luminous cyan
**core ring** centre-screen (SVG arcs, slow rotation; pulse amplitude = organ
activity; colour shifts amber when a proposal awaits, red on URGENT alert).
Thin concentric arc gauges around it: memory count, cycle #, cortex tier
health (reflex/core/apex as three dots), ARIA link status, alert count.
JetBrains Mono microtype labels, uppercase, letter-spaced.

Layout (single screen, no tabs):
- **Centre**: the core ring. Click/tap = talk. Voice via Web Speech API
  (SpeechRecognition input, SpeechSynthesis replies — both on-device browser
  features, free). Transcript fades in around the ring, Jarvis-style.
- **Left rail**: live monologue ticker (SITUATE/CARE/... lines scroll up).
- **Right rail**: alerts + pending proposals with ✓/✗ inline.
- **Bottom strip**: last synapse traffic ("ARIA → breakout NVDA +42").
- **Swipe/press `J`**: journal overlay. **Press `A`**: full Agora feed.

PWA: manifest + service worker → installs to the phone home screen like a
native app. Served only on the tailnet, so it needs no login wall of its own,
but the API still requires the shared token header (Module 5).

Responsive: the ring scales; rails collapse to slide-overs on phone.

---

## MODULE 5 — DEPLOYMENT, ACCESS, SECURITY

**Containers** (`atlas/docker-compose.yml`):
- `atlas-mind` (Python: soul + cortex + alert engine + API :8100)
- `atlas-ui` (static build served by Caddy :3100)
- `aria` (trading-intelligence-system: pipeline + brain + API :8000)
- `reflex` (llama.cpp server with the 3B GGUF, CPU)
- shared volumes: `/data` (all state), `/agora`, `/vault` (read-only mount of
  a synced copy of the Obsidian vault — sync via Syncthing from the laptop)

**Host**: any Ubuntu VPS (2 vCPU/4GB, ~$6-12/mo) or a spare mini-PC at home.
The laptop runs NOTHING permanently. `docker compose up -d` + `restart: always`.

**Access from any device**: Tailscale on host + laptop + phone (free tier).
UI at `http://nerve-centre:3100` from anywhere. **Never** port-forward,
**never** expose publicly.

**Security checklist (non-negotiable before first deploy):**
1. All API endpoints require `X-Atlas-Token` (random 32-byte, in `.env`).
2. Secrets ONLY in `.env` / Docker secrets — **migrate the API keys currently
   sitting in plaintext inside START_ARIA.bat, and rotate every one of them,
   especially the Anthropic and Alpaca keys.**
3. ntfy topic = long random string (it is effectively a password).
4. Nightly `tar` of `/data` + `/agora` to a second location (the laptop via
   Syncthing is fine). The mind must survive a dead VPS.
5. Watchdog container: healthchecks every 5 min; on failure restarts the
   service and fires an URGENT alert ("I was down, I'm back").

---

## MODULE 6 — THE UNIVERSITY PIPELINE (how