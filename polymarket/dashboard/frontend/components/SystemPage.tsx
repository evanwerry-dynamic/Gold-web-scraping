"use client";

const G     = "#00ff88";
const DIM   = "#00ff8833";
const GOLD  = "#ffd700";
const CYAN  = "#00ccff";
const CORAL = "#ff6b6b";
const PURPLE= "#b388ff";
const SKY   = "#87d7f0";
const mono  = '"JetBrains Mono", "Fira Code", "Courier New", monospace';

function H2({ children, color = G }: { children: React.ReactNode; color?: string }) {
  return (
    <div style={{
      color, fontSize: 9, letterSpacing: "0.22em", textTransform: "uppercase" as const,
      borderBottom: `1px solid ${color}33`, paddingBottom: 7, marginBottom: 14,
      display: "flex", alignItems: "center", gap: 8,
    }}>
      <span style={{ color: color + "88" }}>◆</span> {children}
    </div>
  );
}

function Block({ children }: { children: React.ReactNode }) {
  return <div style={{ marginBottom: 32 }}>{children}</div>;
}

function Row({ label, val, color = G, note }: { label: string; val: string; color?: string; note?: string }) {
  return (
    <div style={{ display: "flex", gap: 8, padding: "6px 0", borderBottom: "1px solid #0d0d0d", alignItems: "flex-start" }}>
      <span style={{ color: "#333", fontSize: 10, width: 190, flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1 }}>
        <span style={{ color, fontSize: 11, fontWeight: 700 }}>{val}</span>
        {note && <div style={{ color: "#2a2a2a", fontSize: 9, marginTop: 2 }}>{note}</div>}
      </div>
    </div>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <pre style={{
      background: "#080808", border: "1px solid #111", padding: "10px 14px",
      fontSize: 10, color: CYAN, overflowX: "auto", margin: "8px 0",
      lineHeight: 1.6, fontFamily: mono,
    }}>{children}</pre>
  );
}

function Note({ children, color = "#333" }: { children: React.ReactNode; color?: string }) {
  return (
    <div style={{
      borderLeft: `2px solid ${color}`, paddingLeft: 10,
      color, fontSize: 10, margin: "8px 0", lineHeight: 1.6,
    }}>{children}</div>
  );
}

export function SystemPage() {
  return (
    <div style={{
      flex: 1, overflowY: "auto", padding: "24px 28px",
      background: "#050505", fontFamily: mono, fontSize: 12, color: G,
    }}>
      <div style={{ marginBottom: 28 }}>
        <div style={{ color: "#222", fontSize: 9, letterSpacing: "0.2em", textTransform: "uppercase", marginBottom: 4 }}>
          Mad Scientist · System Documentation
        </div>
        <div style={{ color: G, fontSize: 22, fontWeight: 700 }}>How This Bot Works</div>
        <div style={{ color: "#333", fontSize: 11, marginTop: 6 }}>
          Architecture · Data Feeds · Strategy Logic · Risk System · Edge Explained
        </div>
      </div>

      {/* ── ARCHITECTURE ── */}
      <Block>
        <H2>Architecture — The Big Picture</H2>
        <Note color={G + "66"}>
          The bot is a single Python asyncio event loop running 12 concurrent coroutines.
          No threads. No schedulers. Everything is event-driven.
          One loop processes Binance price ticks, orderbook updates, strategy signals,
          order submissions, redemptions, sanity checks, and dashboard broadcasts — all simultaneously.
        </Note>
        <Code>{`asyncio.gather(
  binance_ws_loop(oracle),      # ← 1s BTC price + rolling volatility
  clob_ws_loop(oracle),         # ← live orderbook bids/asks
  chainlink_rtds_loop(oracle),  # ← window open/close timestamps
  signal_loop(oracle, queue),   # ← Strategy A: late-window momentum
  maker_loop(oracle, queue),    # ← Strategy B: market making
  arb_loop(oracle, queue),      # ← Strategy C: bundle arbitrage
  oms_loop(queue, oracle),      # ← order lifecycle management
  redeem_loop(oracle),          # ← claim winning positions → pUSD
  sanity_loop(oracle),          # ← reconcile positions, check gas
  persist_loop(oracle),         # ← crash-recovery state to disk
  calibrator_loop(),            # ← Claude nightly self-improvement
  dashboard_broadcast(oracle),  # ← push state to WebSocket clients
)`}</Code>
        <div style={{ color: "#333", fontSize: 10, marginTop: 8 }}>
          All 12 coroutines share a single <span style={{ color: CYAN }}>OracleBuffer</span> — an in-memory dataclass holding BTC price, active market details, open positions, bankroll, and volatility estimates. Written only by the three feed loops. Read by everything else.
        </div>
      </Block>

      {/* ── DATA FEEDS ── */}
      <Block>
        <H2 color={CYAN}>Data Feeds — What the Bot Sees</H2>

        <div style={{ marginBottom: 16 }}>
          <div style={{ color: CYAN, fontSize: 11, fontWeight: 700, marginBottom: 6 }}>1. Binance WebSocket — BTC/USDT 1-Second Candles</div>
          <Row label="URL" val="wss://stream.binance.com/ws/btcusdt@kline_1s" color={CYAN} />
          <Row label="What it provides" val="BTC close price every second, zero auth required" />
          <Row label="Reconnect logic" val="Auto-reconnect on any error, 3s backoff" />
          <Row label="Freshness watchdog" val="If no real message in 15s → force reconnect" note="Heartbeat/ping frames don't count as real data" />
          <Row label="What it writes" val="oracle.btc_price, oracle.vol_estimator, oracle.last_binance_ts" />
          <Note color={G + "44"}>
            Every price tick also feeds the BinanceVolEstimator — a 30-sample rolling window of log-returns (ln(p_t / p_t-1)). The standard deviation of those 30 returns is the per-second realized volatility σ used in the fair value model.
          </Note>
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ color: CYAN, fontSize: 11, fontWeight: 700, marginBottom: 6 }}>2. Polymarket CLOB WebSocket — Live Orderbook</div>
          <Row label="URL" val="wss://ws-subscriptions-clob.polymarket.com/ws/market" color={CYAN} />
          <Row label="What it provides" val="Real-time best bid/ask for YES and NO tokens" />
          <Row label="Auth required" val="None — public market data feed" />
          <Row label="Subscription" val="Subscribes to yes_token_id + no_token_id of active window" />
          <Row label="What it writes" val="oracle.active_market.yes_ask, yes_bid, no_ask, no_bid, ask_depth" />
          <Row label="Fill events" val="CLOB fill events update open_positions (reconciled in sanity_loop)" />
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ color: CYAN, fontSize: 11, fontWeight: 700, marginBottom: 6 }}>3. Gamma API — Window Discovery</div>
          <Row label="URL" val="https://gamma-api.polymarket.com/markets" color={CYAN} />
          <Row label="What it provides" val="Current active BTC 5-min Up/Down market metadata" />
          <Row label="Poll interval" val="Every 30 seconds" />
          <Row label="Market slug" val={`btc-updown-5m-{unix_ts_rounded_to_300}`} />
          <Row label="What it writes" val="oracle.active_market (market_id, condition_id, yes/no token IDs, window timestamps)" />
          <Note color={G + "44"}>
            When a new window is detected, the current BTC price is captured as window_open_price. This is the reference point for the delta calculation: every signal in this window is relative to where BTC was at window open.
          </Note>
        </div>
      </Block>

      {/* ── FAIR VALUE MODEL ── */}
      <Block>
        <H2 color={GOLD}>Fair Value Model — The Math</H2>
        <Note color={GOLD + "88"}>
          Standard Black-Scholes is wrong for prediction markets. It assumes a continuous underlying and computes a premium — meaningless for a binary that resolves to $1.00 or $0.00 at a fixed timestamp.
          The correct model: given BTC has moved X% since window open, what is the probability it stays in that direction until resolution?
        </Note>
        <Code>{`# Correct binary probability model
def fair_value_binary(current_price, window_open_price,
                      sigma_per_second, seconds_remaining):

    delta = (current_price - window_open_price) / window_open_price
    z     = delta / (sigma_per_second * sqrt(seconds_remaining))
    return norm.cdf(z)  # Probability UP resolves YES`}</Code>
        <div style={{ color: "#333", fontSize: 10, lineHeight: 1.8, marginTop: 8 }}>
          <div style={{ marginBottom: 6 }}>
            <span style={{ color: GOLD }}>delta</span> — Fractional BTC move since window opened. +0.10% means BTC is 0.10% above where it started this 5-min window.
          </div>
          <div style={{ marginBottom: 6 }}>
            <span style={{ color: GOLD }}>sigma_per_second</span> — Rolling 30s realized vol per second from Binance ticks. Typical value: 0.00015-0.0003 (0.015-0.03%/s).
          </div>
          <div style={{ marginBottom: 6 }}>
            <span style={{ color: GOLD }}>z-score</span> — How many standard deviations the move is relative to remaining uncertainty. At T=5s remaining with delta=0.10% and σ=0.0002, z ≈ 2.23 → P(UP) ≈ 0.987.
          </div>
          <div>
            <span style={{ color: GOLD }}>norm.cdf(z)</span> — Cumulative normal distribution. At T→0 with strong delta, probability collapses to 0 or 1. This is exactly what prediction markets do.
          </div>
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ color: GOLD, fontSize: 11, fontWeight: 700, marginBottom: 6 }}>Dynamic Taker Fee</div>
          <Code>{`fee_rate = 0.036 × (1 - |2 × market_price - 1|)
# At $0.50 entry: fee = 3.60% — edge is destroyed
# At $0.85 entry: fee = 0.72% — manageable
# At $0.92 entry: fee = 0.29% — near-free
# Rule: only trade when ask > $0.70`}</Code>
          <Note color={GOLD + "44"}>
            Polymarket's fee peaks at 50-50 markets (highest uncertainty, highest fee) and drops as price approaches 0 or 1 (high conviction, low fee). The bot never enters below $0.70 — below this level the fee alone exceeds the expected edge.
          </Note>
        </div>
      </Block>

      {/* ── STRATEGIES ── */}
      <Block>
        <H2 color={G}>Strategy A — Late Window Momentum (Primary)</H2>
        <Note color={G + "66"}>
          This is the documented approach behind the $313→$438k Polymarket bot. The edge is simple: BTC is a continuous market. Polymarket's 5-min windows have a fixed resolution timestamp. In the final 10 seconds, if BTC has moved decisively, the binary outcome is nearly certain — but the market price often hasn't fully caught up yet.
        </Note>
        <Row label="When it fires" val="T-10s to T-2s before window close" note="Only the entry window matters. Earlier is noise." />
        <Row label="Signal threshold" val="|window_delta| > 0.10%" note="Below this, Brownian motion can reverse the move before resolution" color={G} />
        <Row label="Entry price" val="Market ask (FOK taker order)" note="Never post a limit — at T<10s there is no time to wait for a fill" />
        <Row label="Order type" val="Fill-Or-Kill (FOK)" note="Either fills immediately at market or cancels. No partial exposure." />
        <Row label="Hold to resolution" val="Always — never exit early" note="At T<5s bid-side liquidity is gone. You cannot exit. Design for this." />
        <Row label="Expected entry price" val="$0.75 – $0.97 on strong moves" note="Weak signals (delta 0.10-0.30%) get $0.75-0.85. Strong moves (>0.30%) get $0.90-0.97." />
        <Row label="Expected win rate" val=">70% when |delta| > 0.10%" note="Calibrated from the documented $313k→$438k bot's 73% win rate" color={G} />
        <Row label="Sizing" val="Quarter-Kelly, max 3% of bankroll" note="Conservative. Preserves capital through losing streaks." />
        <Code>{`# Signal fires when:
secs_left   = window_end_ts - now          # 0 < secs_left ≤ 10
delta       = (btc_price - open_price) / open_price
fair        = norm.cdf(delta / (σ × √secs_left))
net_edge    = fair - market_ask - fee_rate
tradeable   = net_edge > 0.05 and market_ask > 0.70`}</Code>
      </Block>

      <Block>
        <H2 color={CYAN}>Strategy B — Market Making (Secondary)</H2>
        <Note color={CYAN + "66"}>
          Passive income. Post limit orders on both sides of the book and collect maker rebates. Even flat directional P&L is profitable because Polymarket pays you to provide liquidity.
        </Note>
        <Row label="Order type" val="Post-only GTC limit" color={CYAN} />
        <Row label="Finance markets rebate" val="50% of taker fee returned to maker" note="Best markets to quote. High rebate for lower-volume events." color={CYAN} />
        <Row label="Crypto markets rebate" val="20% rebate" note="Higher volume, tighter spreads, but less rebate" />
        <Row label="Sports markets rebate" val="25% rebate" />
        <Row label="Quote management" val="Pull all quotes at T-30s before window close" note="Informed flow arrives in final 30s. You are the sucker if you are still posting at T-30." />
        <Row label="Imbalance signal" val="Pull asks when bid_qty / total_qty > 0.70" note="Heavy buying pressure = your asks are about to get hit at unfavorable price" />
        <Row label="Target spread" val="0.008 – 0.015 (80-150 basis points)" />
      </Block>

      <Block>
        <H2 color={GOLD}>Strategy C — Bundle Arbitrage (Opportunistic)</H2>
        <Note color={GOLD + "66"}>
          Risk-free money when it appears. YES + NO = $1.00 guaranteed at resolution. If you can buy both for less than $1.00 minus fees, you lock in a certain profit regardless of BTC direction.
        </Note>
        <Row label="Entry condition" val="YES_ask + NO_ask < $0.975" color={GOLD} note="Need 2.5% gross spread minimum — fees eat 1-2% at near-50/50 pricing" />
        <Row label="Execution" val="Buy YES and NO simultaneously" note="Both legs must fill. If one fails, cancel the other immediately." />
        <Row label="Profit" val="$1.00 – (YES_ask + NO_ask) – fees" note="Guaranteed at resolution regardless of outcome" />
        <Row label="Frequency" val="Rare — sub-100ms bots capture 73% within 2.7s" note="Only catch what automated arb bots miss. Run as background scanner every 10s." />
        <Row label="Monotonicity arb" val="Also scans for illogical price ladders" note="If P(>X) > P(>X-1) for same underlying, that's a free arb." />
      </Block>

      {/* ── OMS ── */}
      <Block>
        <H2 color={PURPLE}>Order Management System</H2>
        <Note color={PURPLE + "66"}>
          Every order from every strategy goes through a single queue → OMS. This prevents nonce collisions, caps concurrent exposure, and maintains a full audit trail. In paper mode, orders are logged but never submitted to the CLOB.
        </Note>
        <Row label="Concurrency limit" val="3 orders in-flight simultaneously" color={PURPLE} note="asyncio.Semaphore(3) — prevents CLOB rate limits" />
        <Row label="Order lifecycle" val="PENDING → SUBMITTED → PARTIAL → FILLED/REJECTED" />
        <Row label="FOK timeout" val="5 seconds" note="If not filled, consider rejected. Move on." />
        <Row label="GTC timeout" val="30 seconds" note="Cancel any unfilled GTC limit after 30s" />
        <Row label="Paper mode" val="Simulates fills at ask price, logs everything" note="Win/loss is calculated based on resolution price from Chainlink oracle" />
        <Row label="Nonce handling" val="Sequential — each order waits for previous to confirm" note="Prevents 'replacement transaction underpriced' errors on Polygon" />
        <Code>{`# Order lifecycle in paper mode:
PENDING   → intent received from strategy queue
SUBMITTED → would-be CLOB submission (logged, not sent)
FILLED    → simulated at ask price at T+0.2s (realistic fill sim)
           → oracle.bankroll -= dollar_size
           → open_positions[market_id] = OpenPosition(...)
           → on resolution: bankroll += shares × 1.0 (if win)`}</Code>
      </Block>

      {/* ── RISK ── */}
      <Block>
        <H2 color={CORAL}>Risk Management System</H2>
        <Note color={CORAL + "66"}>
          The RiskManager is the only component that can veto a trade. It runs before every order leaves the queue. All limits are checked on every call — there is no bypass.
        </Note>
        <Row label="Total loss halt" val="−40% of initial capital → permanent stop" color={CORAL} note="Once hit, bot stops taking new positions forever. Protects seed." />
        <Row label="Peak drawdown halt" val="−25% from highest bankroll → stop" note="Resets when bankroll recovers above threshold" />
        <Row label="Daily loss limit" val="−5% of daily starting balance" note="Resets at midnight UTC. Hard stop for the day." />
        <Row label="Monthly loss limit" val="−15% of month-start balance" note="Prevents catastrophic months from compounding" />
        <Row label="BTC correlation cap" val="20% max bankroll in all BTC positions combined" note="Prevents single-asset blowup. UP + DOWN both count toward the cap." />
        <Row label="Loss velocity" val="5 losses in 30 minutes → 30 min cooling off" note="Streak detection. Markets may be in a regime the model hasn't seen." />
        <Row label="Position scale factor" val="Reduces Kelly fraction after losing streaks" color={GOLD} note="1 loss → 80% size, 2 losses → 60%, 3+ → 40%. Recovers gradually on wins." />
        <Code>{`def kelly_size(fair_prob, market_price, bankroll, scale_factor):
    b       = (1 - market_price) / market_price  # net odds
    q       = 1 - fair_prob
    kelly   = max(0, (b × fair_prob - q) / b)    # full Kelly
    applied = min(kelly × 0.25, 0.03)             # quarter-Kelly, 3% cap
    applied ×= scale_factor                       # streak adjustment
    return bankroll × applied                     # dollar size`}</Code>
      </Block>

      {/* ── REDEMPTION ── */}
      <Block>
        <H2>Redemption Loop — Why This Matters</H2>
        <Note color={G + "44"}>
          Winning Polymarket positions are ERC-1155 conditional tokens. They do not auto-convert to pUSD. Without the redemption loop, your bankroll calculation becomes wrong — the bot thinks it lost money it actually won.
        </Note>
        <Row label="Runs every" val="30 seconds" />
        <Row label="What it does" val="Finds resolved, unredeemed positions → calls CTF Exchange redeem()" color={G} />
        <Row label="On success" val="pUSD credited to wallet, oracle.bankroll += amount, position marked redeemed" />
        <Row label="On failure" val="Logs error, retries next cycle. Non-blocking." />
        <Row label="Critical for" val="Accurate bankroll tracking, Kelly sizing, Mission page P&L" color={GOLD} />
      </Block>

      {/* ── SANITY ── */}
      <Block>
        <H2>Sanity Loop — Ghost Buster</H2>
        <Note color={G + "44"}>
          Network issues, partial fills, and race conditions can create "ghost positions" — trades the bot doesn't know about. Every 60s, the sanity loop calls the CLOB REST API for ground truth and reconciles.
        </Note>
        <Row label="Runs every" val="60 seconds" />
        <Row label="Ghost detection" val="Compares oracle.open_positions vs CLOB API positions" color={CORAL} note="Any position in CLOB not in oracle gets added and logged as GHOST" />
        <Row label="Gas check" val="Reads Polygon MATIC balance. Alerts if < 1.0 POL" note="Zero gas = all on-chain txs silently fail" color={CORAL} />
        <Row label="pUSD allowance" val="Checks CTF Exchange approval. Re-approves if < 50% of bankroll" />
        <Row label="WS freshness" val="Alerts if Binance or CLOB WS has been silent > 30s" note="Silent WebSocket = trading on stale data = guaranteed losses" />
      </Block>

      {/* ── DASHBOARD FLOW ── */}
      <Block>
        <H2 color={SKY}>Dashboard Data Flow</H2>
        <Code>{`Bot (asyncio) ──► OracleBuffer (shared memory)
                         │
              dashboard_broadcast_loop (every 1s)
                         │
              ┌──────────▼──────────┐
              │   FastAPI WebSocket  │   ← Railway backend
              │   /ws endpoint       │
              └──────────┬──────────┘
                         │  JSON messages
              ┌──────────▼──────────────────────┐
              │  useWebSocket.ts hook            │   ← GitHub Pages
              │  requestAnimationFrame batching  │
              └──────────┬──────────────────────┘
                         │
          ┌──────────────┼──────────────────┐
          ▼              ▼                  ▼
    pnlStore       tradesStore        healthStore
    (P&L panel)    (trade feed)       (system panel)`}</Code>
        <Row label="Broadcast interval" val="Every 1 second from bot → all connected browser tabs" />
        <Row label="Message types" val="pnl · trade · position · book · health · strategy" />
        <Row label="Re-render isolation" val="Each panel subscribes to only its Zustand slice" note="A trade firing does NOT re-render the P&L panel. Zero cross-panel thrash." />
        <Row label="rAF batching" val="All WS messages batched in requestAnimationFrame" note="Prevents layout thrash when multiple messages arrive in same tick" />
        <Row label="Reconnect" val="Auto-reconnect every 3s on drop" color={G} />
        <Row label="Demo fallback" val="DemoDataInjector activates after 4s if ws_binance=false" note="Shows realistic simulation when no bot is connected" />
      </Block>

      {/* ── CLAUDE CALIBRATOR ── */}
      <Block>
        <H2 color={PURPLE}>Claude Nightly Calibration</H2>
        <Note color={PURPLE + "66"}>
          Every night at 2am UTC, if there are 20+ trades in the last 24 hours, the bot sends its performance data to Claude and asks whether to adjust strategy thresholds. This is how the bot self-improves over time.
        </Note>
        <Row label="Trigger" val="2am UTC daily, minimum 20 trades" color={PURPLE} />
        <Row label="Input to Claude" val="Win rate, avg predicted edge, avg realized edge, 5 worst trades" />
        <Row label="Output from Claude" val="New values for MIN_DELTA, MIN_EDGE_NET, ENTRY_SECONDS_BEFORE_CLOSE" />
        <Row label="Apply condition" val="Only adjusts if win_rate < 65% or drift < −0.02" note="Stable performance → no changes. Prevents over-fitting." />
        <Row label="Hot-reload" val="New params saved to polymarket/state/strategy_params.json, loaded next tick" />
        <Row label="Cost" val="~$0.05/call on Claude Opus" color={GOLD} note="Requires ANTHROPIC_API_KEY env var in Railway" />
      </Block>

      <div style={{ color: "#111", fontSize: 10, textAlign: "center", paddingBottom: 8 }}>
        MAD SCIENTIST v1.0 · EVERY PIECE IS NECESSARY · NONE IS REDUNDANT
      </div>
    </div>
  );
}
