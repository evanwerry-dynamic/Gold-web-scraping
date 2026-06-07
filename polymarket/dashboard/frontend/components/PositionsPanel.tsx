"use client";
import { usePositionsStore } from "@/store";

export function PositionsPanel() {
  const positions = usePositionsStore((s) => s.positions);
  const entries = Object.values(positions);

  return (
    <div
      style={{
        padding: 12,
        fontSize: 11,
        height: "100%",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ color: "#999", fontSize: 10, letterSpacing: 1, marginBottom: 8 }}>
        OPEN POSITIONS
        <span style={{ color: "#777", marginLeft: 8 }}>{entries.length}</span>
      </div>

      {entries.length === 0 ? (
        <div style={{ color: "#4a4a5a", paddingTop: 8 }}>No open positions</div>
      ) : (
        <div style={{ overflow: "auto", flex: 1 }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ color: "#888", fontSize: 10 }}>
                <th style={{ textAlign: "left", padding: "2px 4px", fontWeight: 400 }}>MARKET</th>
                <th style={{ textAlign: "center", padding: "2px 4px", fontWeight: 400 }}>SIDE</th>
                <th style={{ textAlign: "right", padding: "2px 4px", fontWeight: 400 }}>SH</th>
                <th style={{ textAlign: "right", padding: "2px 4px", fontWeight: 400 }}>COST</th>
                <th style={{ textAlign: "right", padding: "2px 4px", fontWeight: 400 }}>P&L</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((pos) => {
                const pnl = pos.unrealized_pnl;
                return (
                  <tr key={pos.market_id} style={{ borderTop: "1px solid #111" }}>
                    <td
                      style={{
                        padding: "4px",
                        color: "#666",
                        maxWidth: 70,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontSize: 10,
                      }}
                      title={pos.market_id}
                    >
                      {pos.market_id.split("-").slice(-2).join("-")}
                    </td>
                    <td
                      style={{
                        padding: "4px",
                        textAlign: "center",
                        color: pos.side === "YES" ? "#00ff88" : "#ff4466",
                        fontWeight: 700,
                      }}
                    >
                      {pos.side}
                    </td>
                    <td style={{ padding: "4px", textAlign: "right", color: "#666" }}>
                      {pos.shares.toFixed(1)}
                    </td>
                    <td style={{ padding: "4px", textAlign: "right", color: "#999" }}>
                      ${pos.cost_basis.toFixed(0)}
                    </td>
                    <td
                      style={{
                        padding: "4px",
                        textAlign: "right",
                        color: pnl >= 0 ? "#00ff88" : "#ff4466",
                        fontWeight: 700,
                      }}
                    >
                      {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
