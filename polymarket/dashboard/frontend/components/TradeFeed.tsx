"use client";
import { useTradesStore } from "@/store";

export function TradeFeed() {
  const trades = useTradesStore((s) => s.trades);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ color: "#999", fontSize: 10, letterSpacing: 1, padding: "8px 12px 4px" }}>
        LIVE TRADE FEED
      </div>
      <div className="scroll-panel" style={{ flex: 1, padding: "0 12px" }}>
        {trades.length === 0 && (
          <div style={{ color: "#777", padding: "8px 0" }}>Waiting for signals...</div>
        )}
        {trades.map((t, i) => {
          const pnl = t.pnl ?? 0;
          const color = pnl >= 0 ? "#00ff88" : "#ff4466";
          return (
            <div
              key={i}
              style={{
                display: "grid",
                gridTemplateColumns: "50px 60px 80px 50px 1fr",
                gap: 8,
                padding: "3px 0",
                borderBottom: "1px solid #111",
                fontSize: 11,
              }}
            >
              <span style={{ color: "#999" }}>[{t.strategy}]</span>
              <span style={{ color: t.side === "UP" ? "#00ff88" : "#ff4466" }}>
                {t.side}
              </span>
              <span style={{ color: "#888" }}>
                {t.market_id?.split("-").slice(-2).join("-")}
              </span>
              <span style={{ color: "#888" }}>@{t.entry_price.toFixed(2)}</span>
              <span style={{ color, textAlign: "right" }}>
                {pnl >= 0 ? "+" : ""}${Math.abs(pnl).toFixed(2)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
