"use client";
import { useTradesStore, Trade } from "@/store";

const STRAT: Record<string, string> = { A: "MOM", B: "MKT", C: "ARB" };

export function TradeFeed() {
  const trades = useTradesStore((s) => s.trades);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ color: "#bbb", fontSize: 12, letterSpacing: 1, padding: "8px 12px 4px" }}>
        LIVE TRADE FEED
      </div>
      <div className="scroll-panel" style={{ flex: 1, overflowY: "auto", padding: "0 10px" }}>
        {trades.length === 0 ? (
          <div style={{ color: "#444", padding: "8px 0", fontSize: 11 }}>
            Waiting for signals...
          </div>
        ) : (
          trades.map((t: Trade, i: number) => {
            const pnl = t.pnl;
            const pnlColor = pnl === null ? "#555" : pnl >= 0 ? "#00ff88" : "#ff4466";
            const sideColor = ["UP", "YES"].includes(t.side) ? "#00ff88" : "#ff9900";
            return (
              <div
                key={t.id ?? i}
                style={{
                  padding: "5px 0",
                  borderBottom: "1px solid #141414",
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                }}
              >
                {/* Row 1: badge + side + P&L */}
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{
                    background: "#1a1a1a",
                    color: "#00ff88",
                    fontSize: 9,
                    fontWeight: 700,
                    padding: "1px 5px",
                    letterSpacing: "0.1em",
                    flexShrink: 0,
                  }}>
                    {STRAT[t.strategy] ?? t.strategy}
                  </span>
                  <span style={{ color: sideColor, fontWeight: 700, fontSize: 12, flexShrink: 0 }}>
                    {t.side}
                  </span>
                  <span style={{ flex: 1 }} />
                  <span style={{ color: pnlColor, fontWeight: 700, fontSize: 12 }}>
                    {pnl === null ? "OPEN" : `${pnl >= 0 ? "+" : ""}$${Math.abs(pnl).toFixed(2)}`}
                  </span>
                </div>
                {/* Row 2: entry → fair, size */}
                <div style={{ display: "flex", gap: 8, fontSize: 10, color: "#444" }}>
                  <span>
                    {(t.entry_price ?? 0).toFixed(3)}
                    {t.fair_value ? <span style={{ color: "#2a2a2a" }}> → {t.fair_value.toFixed(3)}</span> : null}
                  </span>
                  <span style={{ flex: 1 }} />
                  <span style={{ color: "#333" }}>${(t.dollar_size ?? 0).toFixed(2)}</span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
