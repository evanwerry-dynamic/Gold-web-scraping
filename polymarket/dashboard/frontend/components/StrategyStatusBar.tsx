"use client";
import { useHealthStore } from "@/store";

const PHASES = ["SCAN", "FAIR", "EDGE", "LIMIT", "FILL", "HOLD"] as const;

export function StrategyStatusBar() {
  const phase = useHealthStore((s) => s.health.strategy_phase);

  return (
    <div
      style={{
        display: "flex",
        gap: 4,
        padding: "6px 16px",
        borderBottom: "1px solid #1e1e1e",
        background: "#080808",
        alignItems: "center",
      }}
    >
      <span style={{ color: "#888", fontSize: 12, marginRight: 8 }}>STRATEGY</span>
      {PHASES.map((p, i) => {
        const isActive = p === phase;
        const isPast = PHASES.indexOf(phase as typeof PHASES[number]) > i;
        return (
          <div key={p} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span
              style={{
                padding: "2px 8px",
                borderRadius: 3,
                fontSize: 12,
                fontWeight: 700,
                letterSpacing: 1,
                border: `1px solid ${isActive ? "#00ff88" : isPast ? "#1e3a1e" : "#1e1e1e"}`,
                color: isActive ? "#00ff88" : isPast ? "#2a6b2a" : "#444",
                background: isActive ? "#001a00" : "transparent",
                transition: "all 0.15s",
              }}
            >
              {p}
            </span>
            {i < PHASES.length - 1 && (
              <span style={{ color: "#777", fontSize: 10 }}>▶</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
