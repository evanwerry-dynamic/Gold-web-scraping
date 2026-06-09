"use client";
import { useState } from "react";
import { usePnlStore, useHealthStore } from "@/store";

export function PnLHeader() {
  const pnl = usePnlStore((s) => s.pnl);
  const halted = useHealthStore((s) => s.health.halted);
  const [toggling, setToggling] = useState(false);

  async function toggleHalt() {
    if (toggling) return;
    setToggling(true);
    try {
      const endpoint = halted ? "/resume" : "/halt";
      await fetch(endpoint, { method: "POST" });
    } catch {
      // Bridge will push updated halt status via health event
    } finally {
      setToggling(false);
    }
  }

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
        background: "#111",
      }}
    >
      {/* Main P&L */}
      <div>
        <span style={{ color: "#bbb", fontSize: 12, marginRight: 6 }}>TOTAL P&L</span>
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
        <span style={{ color: "#bbb", fontSize: 12, marginRight: 4 }}>TODAY</span>
        <span style={{ color: todayColor, fontSize: 14 }}>
          {todaySign}${Math.abs(pnl.today).toFixed(2)}
        </span>
      </div>

      {/* Bankroll */}
      <div>
        <span style={{ color: "#bbb", fontSize: 12, marginRight: 4 }}>BANKROLL</span>
        <span style={{ color: "#ccc", fontSize: 14 }}>
          ${pnl.bankroll.toLocaleString("en-US", { minimumFractionDigits: 2 })}
        </span>
      </div>

      {/* Drawdown */}
      <div>
        <span style={{ color: "#bbb", fontSize: 12, marginRight: 4 }}>DD</span>
        <span style={{ color: pnl.drawdown_pct > 10 ? "#ff4466" : "#888", fontSize: 14 }}>
          {pnl.drawdown_pct.toFixed(1)}%
        </span>
      </div>

      {/* HALT / RESUME button */}
      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
        <button
          onClick={toggleHalt}
          disabled={toggling}
          style={{
            padding: "4px 14px",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.1em",
            border: "1px solid",
            borderRadius: 3,
            cursor: toggling ? "wait" : "pointer",
            background: halted ? "#ff4466" : "transparent",
            borderColor: halted ? "#ff4466" : "#444",
            color: halted ? "#fff" : "#888",
            transition: "all 0.15s",
          }}
        >
          {halted ? "⏸ HALTED — CLICK TO RESUME" : "HALT"}
        </button>

        {/* Live dot */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span
            className="live-dot"
            style={{
              width: 6, height: 6, borderRadius: "50%",
              background: halted ? "#ff4466" : "#00ff88",
              display: "inline-block",
            }}
          />
          <span style={{ color: "#bbb", fontSize: 10 }}>{halted ? "HALTED" : "LIVE"}</span>
        </div>
      </div>
    </div>
  );
}
