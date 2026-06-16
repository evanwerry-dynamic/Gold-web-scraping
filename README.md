# Gold Web Scraping + Mad Scientist Polymarket Bot

Two projects in one repo:

1. **Gold Scraper** — daily OHLC price scraper for `investing.com` gold data  
2. **Mad Scientist** — production-ready Polymarket trading bot targeting BTC 5-minute Up/Down markets with a live dashboard

---

## Table of Contents

- [Gold Scraper](#gold-scraper)
- [Mad Scientist — Polymarket Bot](#mad-scientist--polymarket-bot)
  - [Strategy Overview](#strategy-overview)
  - [Architecture](#architecture)
  - [Quick Start (Paper Trading)](#quick-start-paper-trading)
  - [Live Dashboard](#live-dashboard)
  - [Going Live](#going-live)
  - [Environment Variables](#environment-variables)
  - [Deploy to Railway](#deploy-to-railway)
  - [Risk Management](#risk-management)
  - [Project Structure](#project-structure)
- [Development](#development)

---

## Gold Scraper

A Python scraper for gold commodity prices from [investing.com](https://www.investing.com/commodities/gold).

### Features
- OHLC extraction from the investing.com historical data table
- Output formats: JSON, CSV, lines
- Daily scheduled scraping (5 PM UTC default)
- JSON data persistence

### Install & Run

```bash
git clone https://github.com/evanwerry-dynamic/gold-web-scraping.git
cd gold-web-scraping
pip install -r requirements.txt

# Single fetch
python -m scraper "https://www.investing.com/commodities/gold" --preset investing-gold

# Start daily scheduler
python run_scheduler.py
```

Data is saved to `data/gold_prices.json`.

---

## Mad Scientist — Polymarket Bot

An asyncio trading bot for [Polymarket](https://polymarket.com) BTC 5-minute Up/Down binary markets. Designed to capture the late-window momentum edge: enter T-10 seconds before close when BTC has moved decisively, hold to resolution.

> **Paper trading mode is on by default. No real funds are touched until you explicitly set `PAPER_TRADING=false` and provide wallet credentials.**

### Strategy Overview

#### Strategy A — Late-Window Momentum (Primary)
The main edge: at T-10 seconds before a 5-minute window closes, BTC has usually committed to a direction. Entry then is high-conviction.

**Fair value model** (correct for binary prediction markets — not Black-Scholes):
```
P(UP) = N( window_delta / (σ_realized × √seconds_remaining) )
```
Where:
- `window_delta` = `(current_btc - window_open_btc) / window_open_btc`
- `σ_realized` = rolling 30-second realized vol from 1-second Binance candles
- At T→0 with strong delta, probability collapses to 0 or 1

**Entry filters:**
- `|window_delta| > 0.10%` — minimum conviction threshold
- Entry price `> $0.70` — avoids the 3.15% dynamic fee at $0.50
- Net edge `> 5¢` after fees — only trade when it's worth it

**Sizing:** Quarter-Kelly, hard-capped at 3% of bankroll per trade.

#### Strategy B — Market Making with Rebates (Secondary)
Post-only limit orders earn 20–50% maker rebates depending on market category. Net positive even at flat directional P&L.

#### Strategy C — YES+NO Bundle Arbitrage (Tertiary)
Buy YES + NO when combined cost < $1.00 minus fees. Risk-free, rare.

### Architecture

```
asyncio.gather() — single event loop, 12 concurrent coroutines

  binance_ws_loop      ← 1s BTC candles + rolling realized vol
  clob_ws_loop         ← Polymarket orderbook + fill events
  chainlink_rtds_loop  ← Window open/close timing (Gamma API)
         │
         ▼
    OracleBuffer        ← shared state (thread-safe read-mostly)
         │
    ┌────┴────────────────────────┐
    ▼             ▼              ▼
  signal_loop   maker_loop   arb_loop
  (Strategy A)  (Strategy B) (Strategy C)
         │
    order_queue → RiskManager → OMS
                                 │
                    ┌────────────┴──────────────┐
                    ▼                           ▼
               redeem_loop               sanity_loop
          (claim ERC-1155 tokens)   (ghost positions, gas, health)
                    │
              persist_loop + calibrator_loop
                    │
         FastAPI WebSocket Bridge
                    │
           Next.js 15 Dashboard
```

All coroutines are wrapped in a `_guard()` restart loop — a single crash doesn't kill the bot.

### Quick Start (Paper Trading)

```bash
# 1. Clone and install
git clone https://github.com/evanwerry-dynamic/gold-web-scraping.git
cd gold-web-scraping
pip install -r requirements.txt

# 2. Build the dashboard frontend
cd polymarket/dashboard/frontend
npm install
npm run build
cd ../../..

# 3. Run (paper trading, no credentials needed)
python run_mad_scientist.py
```

Open `http://localhost:8000` for the live dashboard.

**Docker:**
```bash
docker compose up --build
```

### Live Dashboard

The dashboard is a Next.js 15 app that streams data over WebSocket in real-time.

| Panel | What it shows |
|---|---|
| P&L Header | Total P&L, today's P&L, peak drawdown |
| Strategy Status | Current phase: SCAN → FAIR → EDGE → LIMIT → FILL → HOLD |
| Open Positions | Live mark-to-market P&L per position |
| Trade Feed | Every fill with entry price, edge, and final P&L |
| Orderbook Depth | Best bid/ask and book depth |
| BTC Sparkline | Real-time BTC/USD price chart |
| System Health | WebSocket status, bankroll, position count |

A hosted version deploys automatically to GitHub Pages on every push to `main`.

### Going Live

> **Read this section carefully before putting real money in.**

**Prerequisites:**
1. Polygon wallet with 5–10 POL for gas
2. USDC.e on Polygon, wrapped to pUSD via [CollateralOnramp](https://polymarket.com)
3. Polymarket L2 API credentials (generate via `py_clob_client_v2`)

**Recommended minimum bankroll: $500 pUSD.** Below $1,000, individual trades exceed 1% of book depth in thin markets.

```python
# Generate API credentials (one-time setup)
from py_clob_client_v2 import ClobClient
client = ClobClient(host="https://clob.polymarket.com", chain_id=137, key="0x_your_pk")
creds = client.create_or_derive_api_key()
# Save CLOB_API_KEY, CLOB_SECRET, CLOB_PASS_PHRASE, CLOB_NONCE to .env
```

**Test on Amoy testnet first** (`chain_id=80002`, `https://clob-v2.polymarket.com`).

### Environment Variables

Copy `.env.example` to `.env` and fill in your values. **Never commit `.env`.**

```env
# Required for live trading only — leave blank for paper trading
POLYGON_PRIVATE_KEY=0x...
CLOB_API_KEY=...
CLOB_SECRET=...
CLOB_PASS_PHRASE=...
CLOB_NONCE=0

# Bot behaviour
PAPER_TRADING=true          # Set false for live trading
INITIAL_BANKROLL=500        # Starting pUSD (or paper dollars)
MIN_DELTA_THRESHOLD=0.001   # 0.10% — minimum window move to trade
MIN_EDGE_NET=0.05           # 5¢ minimum net edge after fees
ENTRY_SECONDS_BEFORE_CLOSE=60

# External services
BINANCE_WS_URL=wss://stream.binance.com:9443/ws
ANTHROPIC_API_KEY=sk-ant-...  # Optional — enables nightly Claude recalibration

# Infrastructure
DASHBOARD_PORT=8000
DATABASE_URL=postgresql://...  # Optional — persists state across redeploys
```

### Deploy to Railway

The bot is designed for [Railway](https://railway.app) deployment.

1. Fork this repo
2. Create a new Railway project → Deploy from GitHub repo
3. Add a PostgreSQL database (Railway dashboard → + New → Database → PostgreSQL)
4. Set environment variables in Railway's Variables tab
5. Set `PAPER_TRADING=false` and add wallet credentials when ready for live trading

The `railway.toml` configures health checks (`/health`) and restart policy. Railway auto-deploys on every push to `main`.

**Without a database:** State (bankroll, trade history) is lost on every redeploy. The bot warns at startup if no `DATABASE_URL` is set.

### Risk Management

Six circuit breakers. All are active and tested:

| Circuit Breaker | Default Threshold | Action |
|---|---|---|
| Total loss | 40% of initial bankroll | Permanent halt — manual restart required |
| Peak drawdown | 25% from peak bankroll | Pause until bankroll recovers |
| Daily loss | 5% of day-start bankroll | Pause until next UTC day |
| Monthly loss | 15% of month-start bankroll | Pause — manual restart required |
| BTC correlation cap | 20% of bankroll | Block new BTC-correlated positions |
| Loss velocity | 5 losses in 30 minutes | 30-minute cooling-off period |

Position sizing automatically scales down after loss streaks (20% reduction per consecutive loss, floor at 25% of normal size).

The `sanity_loop` runs every 60 seconds:
- Ghost position reconciliation (CLOB ground truth vs bot-tracked)
- MATIC gas balance alert (< 1 POL)
- pUSD allowance auto-reapproval (< 50% of bankroll)
- WebSocket freshness check (stale > 30s triggers critical alert)

### Project Structure

```
Gold-web-scraping/
├── polymarket/
│   ├── main.py                    # asyncio.gather() entry point
│   ├── oracle_buffer.py           # Shared state (BTC price, positions, vol)
│   ├── fair_value.py              # Window-delta binary model + fee formula
│   ├── risk.py                    # RiskManager + kelly_size
│   ├── data.py                    # Trade log (PostgreSQL or JSON file)
│   ├── persist.py                 # Crash recovery state serialization
│   ├── sanity.py                  # Ghost positions, gas, WS health
│   ├── calibrator.py              # Nightly Claude parameter recalibration
│   ├── feeds/
│   │   ├── binance_ws.py          # 1s BTC candles + realized vol
│   │   ├── clob_ws.py             # Polymarket orderbook + fill events
│   │   └── chainlink_rtds.py      # Window timing + paper market simulation
│   ├── strategies/
│   │   ├── signal_loop.py         # Strategy A: late-window momentum
│   │   ├── maker_loop.py          # Strategy B: market making + rebates
│   │   └── arb_loop.py            # Strategy C: YES+NO bundle arbitrage
│   ├── execution/
│   │   ├── oms.py                 # Order lifecycle: PENDING→FILLED
│   │   ├── redeem.py              # ERC-1155 conditional token redemption
│   │   └── wallet.py              # pUSD wrap, approvals, gas monitoring
│   └── dashboard/
│       ├── backend/
│       │   ├── main.py            # FastAPI app + WebSocket + static file serving
│       │   ├── bridge.py          # Bot events → dashboard broadcast
│       │   └── models.py          # Pydantic event types
│       └── frontend/              # Next.js 15 + React 19 + Zustand + ECharts
├── scraper/                       # Gold price scraper (original project)
├── tests/                         # 66 unit tests (fair value, risk, oracle, resolution)
│   ├── test_fair_value.py         # Fair value model bounds + symmetry
│   ├── test_risk.py               # Kelly sizing + all 6 circuit breakers
│   └── test_oracle.py             # Vol estimator + window delta + resolution logic
├── .github/workflows/
│   ├── health_check.yml           # Every 2 hours: 66 tests + integrity checks + Railway ping
│   └── pages.yml                  # GitHub Pages deploy on main push
├── Dockerfile                     # Python 3.11 slim + uvicorn
├── docker-compose.yml
├── railway.toml
├── requirements.txt
├── .env.example                   # Copy to .env — never commit .env
└── run_mad_scientist.py           # Top-level launcher (bot + dashboard)
```

---

## Development

```bash
# Install dependencies
pip install -r requirements.txt
cd polymarket/dashboard/frontend && npm install && cd ../../..

# Run all tests (66 tests: fair value, risk, oracle, resolution logic)
python -m pytest tests/ -v

# Type-check the frontend
cd polymarket/dashboard/frontend && npx tsc --noEmit

# Build frontend (required before running the full app)
cd polymarket/dashboard/frontend && npm run build
```

**Tech stack:**
- **Backend:** Python 3.11, asyncio, FastAPI, uvicorn, websockets, scipy, numpy, web3
- **Frontend:** Next.js 15, React 19, TypeScript, Zustand, Tailwind v4, ECharts, TradingView Lightweight Charts
- **Persistence:** PostgreSQL (production) / JSON files (fallback)
- **Deployment:** Railway (backend + bot) + GitHub Pages (frontend static export)

**Adding a new strategy:**
1. Create `polymarket/strategies/your_loop.py` with `async def your_loop(oracle, order_queue, risk_mgr)`
2. Add to `asyncio.gather()` in `polymarket/main.py`
3. Push order dicts to `order_queue` — the OMS handles submission, lifecycle, and paper simulation

**WebSocket event format** — frontend switches on `type`:

```json
{ "type": "trade",    "data": { "id", "market_id", "side", "entry_price", "pnl", ... } }
{ "type": "pnl",      "data": { "total", "today", "bankroll", "peak", "drawdown_pct" } }
{ "type": "position", "data": { "market_id", "side", "shares", "unrealized_pnl", ... } }
{ "type": "book",     "data": { "yes_bid", "yes_ask", "no_bid", "no_ask", "seconds_remaining" } }
{ "type": "health",   "data": { "ws_binance", "ws_clob", "open_positions", "strategy_phase" } }
```

---

## Disclaimer

This software is for educational and research purposes. Prediction market trading involves substantial risk of financial loss. Always start with paper trading. Never trade with money you cannot afford to lose. This is not financial advice.
