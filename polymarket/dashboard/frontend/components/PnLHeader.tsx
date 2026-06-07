"use client";
import { usePnlStore } from "@/store";

export function PnLHeader() {
  const pnl = usePnlStore((s) => s.pnl);

  const todaySign = pnl.today >= 0 ? "+" : "";
  const todayColor = pnl.today >= 0 ? "#00ff88" : "#ff4466";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 24,
        padding: "8px 16px",
        borderBottom: "1px solid #1e1e1e",
        background: "#0a0a0a",
      }}
    >
      {/* Main P&L */}
      <div>
        <span style={{ color: "#999", fontSize: 12, marginRight: 6 }}>TOTAL P&L</span>
        <span
          style={{
            fontSize: 28,
            fontWeight: 700,
            color: pnl.total >= 0 ? "#00ff88" : "#ff4466",
            letterSpacing: "-1px",
          }}
        >
          {pnl.total >= 0 ? "+" : ""}${pnl.total.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      </div>

      {/* Today */}
      <div>
        <span style={{ color: "#999", fontSize: 12, marginRight: 4 }}>TODAY</span>
        <span style={{ color: todayColor, fontSize: 14 }}>
          {todaySign}${Math.abs(pnl.today).toFixed(2)}
        </span>
      </div>

      {/* Bankroll */}
      <div>
        <span style={{ color: "#999", fontSize: 12, marginRight: 4 }}>BANKROLL</span>
        <span style={{ color: "#ccc", fontSize: 14 }}>
          ${pnl.bankroll.toLocaleString("en-US", { minimumFractionDigits: 2 })}
        </span>
      </div>

      {/* Drawdown */}
      <div>
        <span style={{ color: "#999", fontSize: 12, marginRight: 4 }}>DD</span>
        <span style={{ color: pnl.drawdown_pct > 10 ? "#ff4466" : "#888", fontSize: 14 }}>
          {pnl.drawdown_pct.toFixed(1)}%
        </span>
      </div>

      {/* Live dot */}
      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
        <span className="live-dot" style={{ width: 6, height: 6, borderRadius: "50%", background: "#00ff88", display: "inline-block" }} />
        <span style={{ color: "#999", fontSize: 10 }}>LIVE</span>
      </div>
    </div>
  );
}
