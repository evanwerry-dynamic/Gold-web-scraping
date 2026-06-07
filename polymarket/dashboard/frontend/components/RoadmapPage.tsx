"use client";

const G = "#00ff88";
const DIM = "#00ff8822";
const GOLD = "#ffd700";
const CYAN = "#00ccff";
const CORAL = "#ff6b6b";
const PURPLE = "#b388ff";
const mono = '"JetBrains Mono", "Fira Code", "Courier New", monospace';

function Section({ title, children, color = G }: { title: string; children: React.ReactNode; color?: string }) {
  return (
    <div style={{ marginBottom: 32 }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 10, marginBottom: 14,
        borderBottom: `1px solid ${color}33`, paddingBottom: 8,
      }}>
        <span style={{ color, fontSize: 9, letterSpacing: "0.2em", textTransform: "uppercase" as const }}>
          ◆ {title}
        </span>
      </div>
      {children}
    </div>
  );
}

function Phase({
  num, name, status, eta, env, steps, outcome,
}: {
  num: string; name: string; status: "done" | "next" | "pending";
  eta: string; env?: { key: string; value: string; note: string }[];
  steps: { action: string; detail: string }[]; outcome: string;
}) {
  const color = status === "done" ? G : status === "next" ? GOLD : "#778";
  const badge = status === "done" ? "✓ COMPLETE" : status === "next" ? "▶ DO THIS NOW" : "○ UPCOMING";
  return (
    <div style={{
      border: `1px solid ${color}33`,
      background: `${color}06`,
      padding: "18px 20px",
      marginBottom: 16,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
        <span style={{ color, fontSize: 20, fontWeight: 700, lineHeight: 1 }}>{num}</span>
        <span style={{ color, fontSize: 13, fontWeight: 700 }}>{name}</span>
        <span style={{
          fontSize: 8, color, border: `1px solid ${color}44`,
          background: `${color}11`, padding: "2px 8px", letterSpacing: "0.12em",
          marginLeft: "auto",
        }}>{badge}</span>
        <span style={{ color: "#777", fontSize: 10 }}>{eta}</span>
      </div>

      {/* Env vars to add */}
      {env && env.length > 0 && (
        <div style={{ background: "#0a0a0a", border: "1px solid #1a1a1a", padding: "10px 14px", marginBottom: 12 }}>
          <div style={{ color: "#777", fontSize: 9, letterSpacing: "0.15em", marginBottom: 8 }}>
            RAILWAY → VARIABLES → ADD:
          </div>
          {env.map(e => (
            <div key={e.key} style={{ marginBottom: 6 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <span style={{ color: CYAN, fontSize: 11 }}>{e.key}</span>
                <span style={{ color: "#777" }}>=</span>
                <span style={{ color: GOLD, fontSize: 11 }}>{e.value}</span>
              </div>
              <div style={{ color: "#777", fontSize: 9, marginLeft: 0, marginTop: 2 }}>{e.note}</div>
            </div>
          ))}
        </div>
      )}

      {/* Steps */}
      <div style={{ marginBottom: 12 }}>
        {steps.map((s, i) => (
          <div key={i} style={{
            display: "flex", gap: 10, padding: "5px 0",
            borderBottom: "1px solid #0f0f0f", alignItems: "flex-start",
          }}>
            <span style={{ color: color + "88", fontSize: 10, width: 16, flexShrink: 0, marginTop: 1 }}>
              {status === "done" ? "✓" : `${i + 1}.`}
            </span>
            <div>
              <span style={{ color: status === "done" ? "#666" : "#ccc", fontSize: 11, fontWeight: 600 }}>
                {s.action}
              </span>
              <div style={{ color: "#777", fontSize: 10, marginTop: 2 }}>{s.detail}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Outcome */}
      <div style={{
        borderLeft: `2px solid ${color}44`, paddingLeft: 10,
        color: color + "99", fontSize: 10, fontStyle: "italic",
      }}>
        Outcome: {outcome}
      </div>
    </div>
  );
}

export function RoadmapPage() {
  return (
    <div style={{
      flex: 1, overflowY: "auto", padding: "24px 28px",
      background: "#050505", fontFamily: mono, fontSize: 12, color: G,
    }}>
      <div style={{ marginBottom: 28 }}>
        <div style={{ color: "#777", fontSize: 9, letterSpacing: "0.2em", textTransform: "uppercase", marginBottom: 4 }}>
          Mad Scientist · Deployment Roadmap
        </div>
        <div style={{ color: G, fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em" }}>
          Paper → Real Money in 5 Phases
        </div>
        <div style={{ color: "#888", fontSize: 11, marginTop: 6 }}>
          Every step is documented. Do them in order. Do not skip.
        </div>
      </div>

      <Section title="Phase Overview">
        {[
          { n: "1", name: "Infrastructure", s: "done",    time: "done" },
          { n: "2", name: "Paper Trading",  s: "next",    time: "today" },
          { n: "3", name: "Wallet Setup",   s: "pending", time: "1-2 days" },
          { n: "4", name: "Live at $500",   s: "pending", time: "after phase 3" },
          { n: "5", name: "$10k Goal",      s: "pending", time: "60 days" },
        ].map(p => {
          const c = p.s === "done" ? G : p.s === "next" ? GOLD : "#666";
          return (
            <div key={p.n} style={{
              display: "flex", gap: 12, alignItems: "center",
              padding: "6px 0", borderBottom: "1px solid #0f0f0f",
            }}>
              <span style={{ color: c, fontWeight: 700, width: 14 }}>{p.n}</span>
              <span style={{ color: c, fontSize: 11, flex: 1 }}>{p.name}</span>
              <span style={{ color: "#777", fontSize: 10 }}>{p.time}</span>
            </div>
          );
        })}
      </Section>

      <Phase
        num="1" name="Infrastructure Live" status="done" eta="complete"
        steps={[
          { action: "Railway backend deployed", detail: "FastAPI + uvicorn serving WebSocket at gold-web-scraping-production.up.railway.app/ws" },
          { action: "GitHub Pages dashboard live", detail: "Next.js static build at evanwerry-dynamic.github.io/Gold-web-scraping" },
          { action: "Auto-deploy wired", detail: "Push to main → Railway rebuilds automatically. GitHub Pages rebuilds on gh-pages push." },
          { action: "Demo data confirmed", detail: "Dashboard shows live demo when no bot is connected. Mission page tracking $10k goal." },
        ]}
        outcome="Dashboard is live and accessible from any device. Backend WebSocket server is healthy."
      />

      <Phase
        num="2" name="Paper Trading" status="next" eta="today — 10 minutes"
        env={[
          { key: "POLYGON_PRIVATE_KEY", value: "0x<throwaway_wallet>", note: "Generate a new wallet in MetaMask. Copy the private key. No funds needed — paper mode never submits real orders." },
          { key: "INITIAL_BANKROLL", value: "500", note: "Starting bankroll in pUSD for paper simulation. Set to whatever you plan to deposit live." },
          { key: "PAPER_TRADING", value: "true", note: "Default is already true. Explicit here for clarity." },
        ]}
        steps={[
          { action: "Generate a throwaway wallet", detail: "Open MetaMask → Create new account → copy the private key from Account Details. This is only used to initialize the bot identity. It does not need funds." },
          { action: "Add env vars to Railway", detail: "Railway dashboard → your service → Variables tab → add the three vars above → Save. Railway auto-redeploys." },
          { action: "Watch the Deploy tab", detail: "New deployment triggers. Build takes ~60s. Look for: 'Mad Scientist starting in PAPER TRADING mode' and 'Binance WS connected' in logs." },
          { action: "Open the dashboard", detail: "Switch to ▶ Live tab. After 4s the demo data injector will stop and real data from Binance will appear. BTC price starts updating every second." },
          { action: "Wait for first paper trade", detail: "Strategy A fires at T-10s before each 5-min window close when |window_delta| > 0.10%. In active markets this fires several times per hour. Watch the trade feed." },
        ]}
        outcome="Bot is running in full simulation. Every trade logged. Mission page P&L updates with paper results. No real money at risk."
      />

      <Phase
        num="3" name="Wallet & Credentials Setup" status="pending" eta="1-2 days"
        steps={[
          { action: "Fund wallet with POL for gas", detail: "Buy 5-10 POL (Polygon) on any exchange (Coinbase, Kraken). Send to your wallet address on Polygon network. POL = gas token. Without it every on-chain transaction fails silently." },
          { action: "Get USDC on Polygon", detail: "Buy USDC and bridge to Polygon, or buy directly on Polygon via Coinbase. You need this as your starting capital." },
          { action: "Wrap USDC → pUSD", detail: "Call wrap() on the CollateralOnramp contract (0x93070a847efEf7F70739046A929D47a521F5B8ee). The bot's wallet.py handles this. pUSD is the collateral Polymarket CLOB V2 requires — USDC direct is rejected." },
          { action: "Approve CTF Exchange", detail: "One-time max approval of pUSD spend by CTF Exchange V2 (0xE111180000d2663C0091e4f400237545B87B996B). wallet.py handles this automatically on first run." },
          { action: "Generate CLOB API credentials", detail: "Go to clob.polymarket.com → connect your wallet → Settings → API Keys → Generate. You get: API_KEY, SECRET, PASS_PHRASE. These are L2 credentials — they authorize order submission without signing every tx." },
          { action: "Add all credentials to Railway", detail: "POLYGON_PRIVATE_KEY (real wallet now), CLOB_API_KEY, CLOB_SECRET, CLOB_PASS_PHRASE. Keep PAPER_TRADING=true until you validate in phase 4." },
          { action: "Add polymarket-apis to requirements", detail: "pip install polymarket-apis==2.2.0. This is the CLOB client the bot uses for order submission. Add to requirements.txt then push to main." },
        ]}
        outcome="Wallet funded. pUSD ready. API credentials active. Bot can submit real orders but still won't until PAPER_TRADING=false."
      />

      <Phase
        num="4" name="Go Live at $500" status="pending" eta="after phase 3"
        env={[
          { key: "PAPER_TRADING", value: "false", note: "This is the only change needed. All other vars are already set from phase 3." },
          { key: "INITIAL_BANKROLL", value: "500", note: "Match this to your actual pUSD deposit. Kelly sizing scales from this." },
        ]}
        steps={[
          { action: "Confirm pUSD balance", detail: "Check Railway logs for 'Bankroll: $X pUSD'. Should match your deposit. If zero, wrap step from phase 3 may not have run." },
          { action: "Set PAPER_TRADING=false in Railway", detail: "Railway → Variables → change PAPER_TRADING to false → save. Railway redeploys. This is the moment real orders begin." },
          { action: "Watch first real trade", detail: "First Strategy A signal will fire at next 5-min window close with strong BTC move. Max order size = $15 (3% of $500). Watch the trade feed for LIVE (not paper) label." },
          { action: "Validate redemption loop", detail: "After your first winning trade resolves, the redeem_loop (runs every 30s) should claim your pUSD. Watch logs for 'Redeemed X pUSD for market_id'. This confirms the full cycle works." },
          { action: "Run for 48 hours", detail: "Let the bot trade for 2 days without touching it. Review: win rate (target >70% when |delta|>0.10%), average edge realized vs predicted, any ghost positions in sanity logs." },
          { action: "Validate matches paper results", detail: "Compare live 48h win rate to paper trading win rate. If within 20%, the model is working. If significantly worse, check fees, slippage, or latency issues before scaling." },
        ]}
        outcome="First real trades confirmed. Full cycle validated: signal → order → fill → redemption → bankroll update. Foundation for scaling."
      />

      <Phase
        num="5" name="Scale to $10k Resort Goal" status="pending" eta="60 days"
        steps={[
          { action: "Week 1-2: $500 → $1,000", detail: "At ~5% daily edge with Kelly sizing, $500 compounds. Target: double in 2 weeks. Add Strategy B (market making) once Strategy A is proven stable." },
          { action: "Week 3-4: $1,000 → $2,500", detail: "Increase INITIAL_BANKROLL env var to match actual balance. Kelly sizing scales with bankroll — larger positions on the same edge." },
          { action: "Week 5-6: $2,500 → $5,000", detail: "Enable Claude nightly calibration (set ANTHROPIC_API_KEY in Railway). Bot self-adjusts thresholds based on realized vs predicted edge. Strategy C (bundle arb) runs opportunistically." },
          { action: "Week 7-8: $5,000 → $10,000", detail: "Final leg. Daily need: ~$357/day. At this bankroll, 3% Kelly = $150-300/trade. A few decisive BTC moves per day is enough." },
          { action: "Withdraw and book", detail: "When bankroll hits $10,000: withdraw pUSD → USDC → bank. Book flights and overwater villa. The bot paid for the Maldives." },
        ]}
        outcome="$10,000 in pUSD. Maldives trip booked. All-inclusive overwater bungalow. Mission complete."
      />

      {/* Key risks */}
      <Section title="What Can Go Wrong" color={CORAL}>
        {[
          ["MATIC/POL runs out", "Bot silently stops all on-chain txs. Monitor Railway logs for 'LOW GAS' warning. Keep 5+ POL in wallet at all times.", CORAL],
          ["Win rate drops below 65%", "Claude nightly calibration (Phase 5) detects this and tightens MIN_DELTA threshold. Can also manually increase ENTRY_SECONDS_BEFORE_CLOSE env var.", GOLD],
          ["pUSD allowance expires", "Sanity loop auto-reapproves every 60s check. If it fails, manually call approve(CTF_EXCHANGE_V2, max) from wallet.", CORAL],
          ["Partial fills leave ghost positions", "Sanity loop reconciles every 60s against CLOB ground truth. Check logs for 'GHOST POSITION DETECTED'.", GOLD],
          ["BTC market goes flat (no delta)", "No trades fire. Normal. Bot is designed to sit out quiet periods — this is correct behavior, not a bug.", G],
        ].map(([risk, fix, color]) => (
          <div key={risk as string} style={{
            borderLeft: `2px solid ${color as string}44`, paddingLeft: 12,
            marginBottom: 12, paddingTop: 4, paddingBottom: 4,
          }}>
            <div style={{ color: color as string, fontSize: 11, fontWeight: 700, marginBottom: 3 }}>{risk}</div>
            <div style={{ color: "#888", fontSize: 10 }}>{fix}</div>
          </div>
        ))}
      </Section>

      <div style={{ color: "#999", fontSize: 10, textAlign: "center", paddingBottom: 8 }}>
        MAD SCIENTIST v1.0 · FOLLOW THE PHASES · DO NOT SKIP STEPS
      </div>
    </div>
  );
}
