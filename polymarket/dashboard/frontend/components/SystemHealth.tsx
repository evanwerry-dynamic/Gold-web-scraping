"use client";
import { useHealthStore } from "@/store";

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={ok ? "live-dot" : undefined}
      style={{
        display: "inline-block",
        width: 7,
        height: 7,
        borderRadius: "50%",
        background: ok ? "#00ff88" : "#ff4466",
        marginRight: 5,
      }}
    />
  );
}

export function SystemHealth() {
  const h = useHealthStore((s) => s.health);

  return (
    <div style={{ padding: 12, borderLeft: "1px solid #1e1e1e", fontSize: 11 }}>
      <div style={{ color: "#999", marginBottom: 8, fontSize: 12, letterSpacing: 1 }}>
        SYSTEM HEALTH
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div>
          <Dot ok={h.ws_binance} />
          <span style={{ color: "#888" }}>BINANCE WS</span>
        </div>
        <div>
          <Dot ok={h.ws_clob} />
          <span style={{ color: "#888" }}>CLOB WS</span>
        </div>
        <div style={{ marginTop: 8, color: "#999", fontSize: 10 }}>BTC PRICE</div>
        <div style={{ color: "#00ff88", fontSize: 16, fontWeight: 700 }}>
          ${h.btc_price.toLocaleString("en-US")}
        </div>
        <div style={{ marginTop: 4, color: "#999", fontSize: 10 }}>OPEN POSITIONS</div>
        <div style={{ color: "#ccc", fontSize: 14 }}>{h.open_positions}</div>
      </div>
    </div>
  );
}
