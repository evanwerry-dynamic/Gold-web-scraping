"use client";
import { useTradesStore } from "@/store";

const STRATEGIES = ["A", "B", "C"] as const;

export function PerformanceMatrix() {
  const trades = useTradesStore((s) => s.trades);

  const stats = STRATEGIES.map((s) => {
    // Only count terminal events (redeem = resolved win/loss, expired = orphan-reconciled loss).
    // Raw buy records (no action) are excluded — they double-count with their terminal event
    // and stay "OPEN" (pnl=null) until the window closes, skewing win% and P&L.
    const TERMINAL = ["redeem", "expired", "settle"];
    const closed = trades.filter(
      (t) => t.strategy === s && TERMINAL.includes(t.action ?? "") && (t.dollar_size ?? 0) > 0
    );
    const wins = closed.filter((t) => (t.pnl ?? 0) > 0).length;
    const total_pnl = closed.reduce((sum, t) => sum + (t.pnl ?? 0), 0);
    return { strategy: s, count: closed.length, closed: closed.length, wins, total_pnl };
  });

  return (
    <div style={{ padding: 12, fontSize: 11 }}>
      <div style={{ color: "#bbb", fontSize: 12, letterSpacing: 1, marginBottom: 8 }}>
        PERFORMANCE MATRIX
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ color: "#aaa", fontSize: 10 }}>
            <th style={{ textAlign: "left", padding: "2px 4px" }}>LAYER</th>
            <th style={{ textAlign: "right", padding: "2px 4px" }}>TRADES</th>
            <th style={{ textAlign: "right", padding: "2px 4px" }}>WIN%</th>
            <th style={{ textAlign: "right", padding: "2px 4px" }}>P&L</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((row) => (
            <tr key={row.strategy} style={{ borderTop: "1px solid #111" }}>
              <td style={{ padding: "4px", color: "#aaa" }}>
                {row.strategy === "A" ? "Momentum" : row.strategy === "B" ? "Market Make" : "Arbitrage"}
              </td>
              <td style={{ padding: "4px", textAlign: "right", color: "#ccc" }}>{row.count}</td>
              <td style={{ padding: "4px", textAlign: "right", color: row.closed > 0 && row.wins / row.closed >= 0.7 ? "#00ff88" : "#888" }}>
                {row.closed > 0 ? `${((row.wins / row.closed) * 100).toFixed(0)}%` : "—"}
              </td>
              <td style={{ padding: "4px", textAlign: "right", color: row.total_pnl >= 0 ? "#00ff88" : "#ff4466" }}>
                {row.total_pnl >= 0 ? "+" : ""}${row.total_pnl.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
