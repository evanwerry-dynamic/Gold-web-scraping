"use client";
import { usePnlStore } from "@/store";

const OCEAN      = "#00c8d4";
const DEEP       = "#041e30";
const TURQ       = "#00e5cc";
const CORAL      = "#ff6b6b";
const SAND       = "#f4c97a";
const SEAFOAM    = "#a7f3e8";
const SKY        = "#87d7f0";
const DIM        = "#9ad0e0";
const HORIZON    = "#0a2d40";

const mono = '"JetBrains Mono", "Fira Code", "Courier New", monospace';

function Wave() {
  return (
    <div style={{ color: OCEAN + "44", fontSize: 11, letterSpacing: 2, margin: "10px 0", userSelect: "none" }}>
      ≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      color: SKY,
      fontSize: 11,
      letterSpacing: "0.22em",
      textTransform: "uppercase",
      marginBottom: 12,
      display: "flex",
      alignItems: "center",
      gap: 8,
    }}>
      <span style={{ color: OCEAN }}>◆</span>
      {children}
      <span style={{ flex: 1, height: 1, background: `linear-gradient(to right, ${OCEAN}44, transparent)` }} />
    </div>
  );
}

function Row({ label, value, sub, valueColor = TURQ }: {
  label: string; value: string; sub?: string; valueColor?: string;
}) {
  return (
    <div style={{
      display: "flex",
      justifyContent: "space-between",
      alignItems: "baseline",
      borderBottom: `1px solid ${DIM}55`,
      padding: "8px 0",
      gap: 12,
    }}>
      <span style={{ color: DIM, fontSize: 12, flexShrink: 0 }}>{label}</span>
      <div style={{ textAlign: "right" }}>
        <span style={{ color: valueColor, fontSize: 13, fontWeight: 700 }}>{value}</span>
        {sub && <div style={{ color: DIM, fontSize: 11, marginTop: 2 }}>{sub}</div>}
      </div>
    </div>
  );
}

function ProgressBar({ pct, color = TURQ }: { pct: number; color?: string }) {
  return (
    <div style={{ height: 6, background: DIM + "55", position: "relative", borderRadius: 0 }}>
      <div style={{
        position: "absolute", left: 0, top: 0, height: "100%",
        width: `${Math.min(100, pct)}%`,
        background: `linear-gradient(to right, ${color}88, ${color})`,
        boxShadow: `0 0 8px ${color}66`,
      }} />
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
      background: `linear-gradient(180deg, ${DEEP} 0%, #051a2e 40%, #061520 100%)`,
      fontFamily: mono,
      fontSize: 13,
      color: SEAFOAM,
    }}>
      {/* Hero */}
      <div style={{
        padding: "32px 28px 20px",
        background: `linear-gradient(180deg, #020e1a 0%, transparent 100%)`,
        borderBottom: `1px solid ${OCEAN}22`,
        textAlign: "center",
      }}>
        <div style={{ color: OCEAN + "88", fontSize: 11, letterSpacing: "0.3em", textTransform: "uppercase", marginBottom: 12 }}>
          ◆ &nbsp; destination locked &nbsp; ◆
        </div>

        {/* ASCII overwater bungalow */}
        <pre style={{
          color: OCEAN + "66",
          fontSize: 11,
          lineHeight: 1.4,
          margin: "0 auto 16px",
          userSelect: "none",
          display: "inline-block",
          textAlign: "left",
        }}>{`        __|__
    ---(___)---
   /    | |    \\
  /  ~~~|_|~~~  \\
 /_______________\\
 |   MALDIVES    |
 |  OVERWATER    |
 |   VILLA  🌴   |
/~~~~~~~~~~~~~~~~~\\
  ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈`}</pre>

        <div style={{
          color: SAND,
          fontSize: 28,
          fontWeight: 700,
          letterSpacing: "-0.02em",
          lineHeight: 1.1,
          marginBottom: 6,
        }}>
          $10,000
        </div>
        <div style={{ color: SKY, fontSize: 12, marginBottom: 4 }}>
          Overwater Bungalow · North Malé Atoll
        </div>
        <div style={{ color: DIM, fontSize: 10 }}>
          Flights · 7 nights all-inclusive · fully autonomous
        </div>
      </div>

      <Wave />

      {/* Progress */}
      <div style={{ padding: "0 28px 20px" }}>
        <SectionLabel>Departure Fund</SectionLabel>

        <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 12 }}>
          <span style={{ color: SAND, fontSize: 26, fontWeight: 700 }}>
            ${current.toLocaleString("en-US", { maximumFractionDigits: 0 })}
          </span>
          <span style={{ color: DIM, fontSize: 12 }}>/ $10,000</span>
          <span style={{ color: pct >= 100 ? TURQ : SAND, fontSize: 13, fontWeight: 700, marginLeft: "auto" }}>
            {pct.toFixed(1)}%
          </span>
        </div>

        <ProgressBar pct={pct} color={SAND} />

        <div style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: 8,
          color: DIM,
          fontSize: 12,
        }}>
          <span>Remaining: <span style={{ color: OCEAN }}>${remaining.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span></span>
          <span>Need <span style={{ color: CORAL }}>${dailyNeeded}/day</span> · {daysLeft} days</span>
        </div>

        <Wave />

        {/* Booking breakdown */}
        <SectionLabel>Resort Breakdown</SectionLabel>
        <Row label="Overwater villa · 7 nights" value="$5,800" valueColor={SEAFOAM} />
        <Row label="Return flights (x2)" value="$2,400" valueColor={SEAFOAM} />
        <Row label="Dining · excursions · spa" value="$1,200" valueColor={SEAFOAM} />
        <Row label="Buffer" value="$600" valueColor={DIM} />
        <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 0", borderTop: `1px solid ${OCEAN}44`, marginTop: 4 }}>
          <span style={{ color: SKY, fontSize: 11, fontWeight: 700 }}>Total target</span>
          <span style={{ color: SAND, fontSize: 14, fontWeight: 700 }}>$10,000</span>
        </div>
      </div>

      <Wave />

      {/* Strategy */}
      <div style={{ padding: "0 28px 20px" }}>
        <SectionLabel>How the Bot Gets There</SectionLabel>
        {[
          { name: "A — Late Window Momentum", alloc: 70, color: TURQ,  note: "BTC moves >0.10% in final 10s · FOK buy · hold to resolution" },
          { name: "B — Market Making",         alloc: 20, color: SKY,   note: "Post-only quotes · 20-50% maker rebate · passive income" },
          { name: "C — Bundle Arbitrage",      alloc: 10, color: OCEAN, note: "YES+NO <$1.00 net · risk-free · rare but free money" },
        ].map((s) => (
          <div key={s.name} style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
              <span style={{ color: s.color, fontSize: 11, fontWeight: 700 }}>{s.name}</span>
              <span style={{ color: DIM, fontSize: 10 }}>{s.alloc}%</span>
            </div>
            <ProgressBar pct={s.alloc} color={s.color} />
            <div style={{ color: DIM, fontSize: 12, marginTop: 4 }}>{s.note}</div>
          </div>
        ))}
      </div>

      <Wave />

      {/* Risk rules */}
      <div style={{ padding: "0 28px 20px" }}>
        <SectionLabel>Inviolable Risk Rules</SectionLabel>
        {[
          ["Max per trade",       "3% bankroll",        "Quarter-Kelly — preserves capital"],
          ["Daily stop-loss",     "−5% / day",          "Locks in — no override, no exceptions"],
          ["Total loss halt",     "−40% all-time",      "Protect the seed money permanently"],
          ["BTC concentration",  "20% max exposure",   "No correlated blowup scenario"],
          ["Loss streak pause",  "5 losses / 30 min",  "Cool-off before re-entering"],
          ["Entry floor",        "Ask > $0.70",        "Fee is 0.54% here vs 3.15% at $0.50"],
        ].map(([rule, limit, why]) => (
          <div key={rule as string} style={{
            display: "flex",
            gap: 8,
            borderBottom: `1px solid ${DIM}33`,
            padding: "7px 0",
            alignItems: "flex-start",
          }}>
            <span style={{ color: DIM, fontSize: 12, width: 130, flexShrink: 0 }}>{rule}</span>
            <span style={{ color: CORAL, fontSize: 11, fontWeight: 700, flex: 1 }}>{limit}</span>
            <span style={{ color: DIM, fontSize: 11, textAlign: "right" }}>{why}</span>
          </div>
        ))}
      </div>

      <Wave />

      {/* Phases */}
      <div style={{ padding: "0 28px 20px" }}>
        <SectionLabel>Mission Phases</SectionLabel>
        {[
          { n: "1", name: "Go Live",        status: "done",    desc: "Dashboard + Railway backend deployed · WebSocket streaming" },
          { n: "2", name: "Paper Trading",  status: "next",    desc: "Add POLYGON_PRIVATE_KEY to Railway · bot runs, no real orders" },
          { n: "3", name: "Wallet Setup",   status: "pending", desc: "5 POL gas · USDC → pUSD · CLOB API credentials" },
          { n: "4", name: "$500 Live",      status: "pending", desc: "First real trades · max $10/order · validate full cycle" },
          { n: "5", name: "$10k Resort",    status: "pending", desc: "Compound to goal · book the overwater villa · fly" },
        ].map((p) => {
          const color = p.status === "done" ? TURQ : p.status === "next" ? SAND : DIM;
          const badge = p.status === "done" ? "✓ LIVE" : p.status === "next" ? "▶ NEXT" : "○";
          return (
            <div key={p.n} style={{
              display: "flex",
              gap: 12,
              borderBottom: `1px solid ${DIM}33`,
              padding: "10px 0",
              alignItems: "flex-start",
            }}>
              <div style={{
                color,
                fontSize: 16,
                fontWeight: 700,
                width: 18,
                flexShrink: 0,
                textShadow: p.status !== "pending" ? `0 0 12px ${color}` : "none",
              }}>{p.n}</div>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                  <span style={{ color, fontSize: 11, fontWeight: 700 }}>{p.name}</span>
                  <span style={{
                    fontSize: 8,
                    color,
                    border: `1px solid ${color}44`,
                    background: color + "11",
                    padding: "1px 6px",
                    letterSpacing: "0.1em",
                  }}>{badge}</span>
                </div>
                <span style={{ color: DIM, fontSize: 10 }}>{p.desc}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Win condition */}
      <div style={{ padding: "0 28px 32px" }}>
        <Wave />
        <div style={{
          border: `1px solid ${OCEAN}44`,
          background: `linear-gradient(135deg, ${OCEAN}08, ${TURQ}04)`,
          padding: "20px 24px",
          textAlign: "center",
          boxShadow: `inset 0 0 40px ${OCEAN}08`,
        }}>
          <div style={{ color: OCEAN, fontSize: 11, letterSpacing: "0.25em", textTransform: "uppercase", marginBottom: 10 }}>
            ≈ &nbsp; win condition &nbsp; ≈
          </div>
          <div style={{ color: SEAFOAM, fontSize: 12, lineHeight: 1.9 }}>
            $10,000 withdrawn to bank.<br />
            <span style={{ color: SKY }}>Flights booked. Villa confirmed.</span><br />
            Turquoise water. Overwater deck.<br />
            <span style={{ color: SAND, fontWeight: 700 }}>The bot paid for all of it.</span>
          </div>
          <div style={{ marginTop: 14, color: OCEAN + "55", fontSize: 12, letterSpacing: 3 }}>
            ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈ ≈
          </div>
        </div>
      </div>
    </div>
  );
}
