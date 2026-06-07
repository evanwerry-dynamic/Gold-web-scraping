"use client";
import { usePnlStore } from "@/store";

const G = "#00ff88";
const DIM = "#00ff8855";
const GOLD = "#ffd700";
const RED = "#ff4444";
const CYAN = "#00ccff";

const S = {
  section: {
    borderLeft: `2px solid ${G}`,
    paddingLeft: 16,
    marginBottom: 28,
  } as React.CSSProperties,
  label: {
    color: DIM,
    fontSize: 10,
    letterSpacing: "0.15em",
    textTransform: "uppercase" as const,
    marginBottom: 6,
  },
  value: {
    color: G,
    fontSize: 22,
    fontWeight: 700,
    letterSpacing: "-0.01em",
  },
  row: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "baseline",
    borderBottom: "1px solid #0f0f0f",
    padding: "7px 0",
    gap: 12,
  } as React.CSSProperties,
  pill: (color: string) => ({
    display: "inline-block",
    background: color + "22",
    border: `1px solid ${color}44`,
    color: color,
    fontSize: 9,
    padding: "2px 8px",
    letterSpacing: "0.12em",
    textTransform: "uppercase" as const,
  }),
};

function Bar({ pct, color = G }: { pct: number; color?: string }) {
  return (
    <div style={{ flex: 1, height: 4, background: "#111", position: "relative", marginLeft: 12 }}>
      <div style={{ position: "absolute", left: 0, top: 0, height: "100%", width: `${Math.min(100, pct)}%`, background: color }} />
    </div>
  );
}

export function VisionPage() {
  const { pnl } = usePnlStore();

  const goal = 10000;
  const current = pnl.bankroll || 0;
  const pct = Math.min(100, (current / goal) * 100);
  const remaining = Math.max(0, goal - current);

  const daysLeft = 60;
  const dailyNeeded = remaining > 0 ? (remaining / daysLeft).toFixed(0) : "0";

  return (
    <div style={{
      flex: 1,
      overflowY: "auto",
      padding: "24px 28px",
      background: "#050505",
    }}>
      {/* Header */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ color: DIM, fontSize: 10, letterSpacing: "0.2em", textTransform: "uppercase", marginBottom: 4 }}>
          Mad Scientist — Autonomous Trading System
        </div>
        <div style={{ color: GOLD, fontSize: 26, fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.2 }}>
          $10,000 in 60 Days
        </div>
        <div style={{ color: "#555", fontSize: 11, marginTop: 6 }}>
          All-inclusive resort. Fully funded by the bot. No compromise.
        </div>
      </div>

      {/* Progress */}
      <div style={{ ...S.section, borderColor: GOLD }}>
        <div style={S.label}>Mission Progress</div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 10 }}>
          <span style={{ ...S.value, color: GOLD }}>${current.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
          <span style={{ color: DIM, fontSize: 12 }}>/ ${goal.toLocaleString()}</span>
          <span style={{ color: GOLD, fontSize: 14, marginLeft: "auto" }}>{pct.toFixed(1)}%</span>
        </div>
        <div style={{ height: 8, background: "#111", position: "relative", marginBottom: 8 }}>
          <div style={{ position: "absolute", left: 0, top: 0, height: "100%", width: `${pct}%`, background: GOLD }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", color: DIM, fontSize: 10 }}>
          <span>Remaining: ${remaining.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
          <span>Need ~${dailyNeeded}/day · {daysLeft} days on clock</span>
        </div>
      </div>

      {/* The Edge */}
      <div style={S.section}>
        <div style={S.label}>The Edge — Why This Works</div>
        {[
          ["Late-window momentum", "BTC moves >0.10% in final 10s → binary resolves with 75%+ certainty", G],
          ["Oracle lag arbitrage", "Chainlink settles 2-5s after window — bot enters at T-10s, captures drift", G],
          ["Dynamic fee filter", "Only trade when entry price >$0.70 — fee drops from 3.15% to 0.54%", G],
          ["Maker rebates", "Post-only limit orders earn 20-50% rebate on every filled quote", CYAN],
          ["YES+NO bundle arb", "Buy both sides <$1.00 net — guaranteed profit, zero directional risk", CYAN],
        ].map(([name, desc, color]) => (
          <div key={name as string} style={S.row}>
            <div>
              <div style={{ color: color as string, fontSize: 11, fontWeight: 700, marginBottom: 2 }}>{name}</div>
              <div style={{ color: "#444", fontSize: 10 }}>{desc}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Strategy breakdown */}
      <div style={S.section}>
        <div style={S.label}>Strategy Allocation</div>
        {[
          { name: "A — Late Window Momentum", target: 70, color: G, note: "Primary · FOK market buy at T-10s" },
          { name: "B — Market Making", target: 20, color: CYAN, note: "Passive · post-only quotes + rebates" },
          { name: "C — Bundle Arbitrage", target: 10, color: GOLD, note: "Opportunistic · risk-free when spread >2.5%" },
        ].map((s) => (
          <div key={s.name} style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <span style={{ color: s.color, fontSize: 11, fontWeight: 700 }}>{s.name}</span>
              <span style={{ color: DIM, fontSize: 10 }}>{s.target}%</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", marginBottom: 2 }}>
              <Bar pct={s.target} color={s.color} />
            </div>
            <div style={{ color: "#333", fontSize: 10 }}>{s.note}</div>
          </div>
        ))}
      </div>

      {/* Risk constitution */}
      <div style={{ ...S.section, borderColor: RED }}>
        <div style={{ ...S.label, color: RED + "99" }}>Risk Constitution — Inviolable Rules</div>
        {[
          ["Max per trade", "3% of bankroll", "Quarter-Kelly sizing"],
          ["Daily loss limit", "5% of bankroll", "Hard stop — no override"],
          ["Total loss halt", "40% of initial capital", "Permanent — protect the seed"],
          ["Correlated exposure", "20% max in BTC positions", "No concentration blowup"],
          ["Loss velocity", "5 losses in 30 min → pause", "Streak protection"],
          ["Entry price floor", "$0.70 minimum", "Fee eats edge below this"],
        ].map(([rule, limit, why]) => (
          <div key={rule as string} style={S.row}>
            <span style={{ color: "#555", fontSize: 10, width: 140, flexShrink: 0 }}>{rule}</span>
            <span style={{ color: RED, fontSize: 11, fontWeight: 700, flex: 1 }}>{limit}</span>
            <span style={{ color: "#333", fontSize: 10 }}>{why}</span>
          </div>
        ))}
      </div>

      {/* Build phases */}
      <div style={S.section}>
        <div style={S.label}>Deployment Phases</div>
        {[
          { phase: "1", name: "Dashboard Live", status: "done", desc: "FastAPI WebSocket + Next.js dashboard on Railway + GitHub Pages" },
          { phase: "2", name: "Paper Trading", status: "next", desc: "Add POLYGON_PRIVATE_KEY to Railway → bot starts in paper mode, no real orders" },
          { phase: "3", name: "Wallet Setup", status: "pending", desc: "Fund wallet with 5 POL gas + wrap USDC→pUSD via CollateralOnramp" },
          { phase: "4", name: "Live at $500", status: "pending", desc: "Deploy with $500 pUSD, max $10/trade — validate fills + redemptions" },
          { phase: "5", name: "Scale to Goal", status: "pending", desc: "Compound winnings — $500→$2k→$5k→$10k over 60 days" },
        ].map((p) => {
          const color = p.status === "done" ? G : p.status === "next" ? GOLD : "#333";
          const badge = p.status === "done" ? "✓ LIVE" : p.status === "next" ? "▶ NEXT" : "○ PENDING";
          return (
            <div key={p.phase} style={{ ...S.row, alignItems: "flex-start" }}>
              <span style={{ color, fontSize: 18, fontWeight: 700, width: 20, flexShrink: 0, marginTop: 1 }}>{p.phase}</span>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                  <span style={{ color, fontSize: 11, fontWeight: 700 }}>{p.name}</span>
                  <span style={S.pill(color)}>{badge}</span>
                </div>
                <span style={{ color: "#333", fontSize: 10 }}>{p.desc}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Win condition */}
      <div style={{
        border: `1px solid ${GOLD}33`,
        background: GOLD + "08",
        padding: "16px 20px",
        marginBottom: 24,
      }}>
        <div style={{ color: GOLD, fontSize: 13, fontWeight: 700, marginBottom: 8, letterSpacing: "0.05em" }}>
          ◈ WIN CONDITION
        </div>
        <div style={{ color: "#888", fontSize: 11, lineHeight: 1.7 }}>
          $10,000 pUSD redeemed → converted to USDC → withdrawn to bank.<br />
          Flight + hotel booked. All-inclusive. Fully autonomous. Bot paid for it.<br />
          <span style={{ color: GOLD }}>Deadline: 60 days from first live trade.</span>
        </div>
      </div>

      <div style={{ color: "#222", fontSize: 10, textAlign: "center", paddingBottom: 8 }}>
        MAD SCIENTIST v1.0 · POLYMARKET BTC 5-MIN UP/DOWN · PAPER MODE ACTIVE
      </div>
    </div>
  );
}
