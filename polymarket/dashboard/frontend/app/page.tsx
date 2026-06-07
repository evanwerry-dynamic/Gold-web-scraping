"use client";
import { useState } from "react";
import { WsInit } from "@/components/WsInit";
import { DemoDataInjector } from "@/components/DemoDataInjector";
import { PnLHeader } from "@/components/PnLHeader";
import { StrategyStatusBar } from "@/components/StrategyStatusBar";
import { PositionsPanel } from "@/components/PositionsPanel";
import { MicrostructureMesh } from "@/components/MicrostructureMesh";
import { OrderbookDepth } from "@/components/OrderbookDepth";
import { TradeFeed } from "@/components/TradeFeed";
import { PriceSparkline } from "@/components/PriceSparkline";
import { PerformanceMatrix } from "@/components/PerformanceMatrix";
import { SystemHealth } from "@/components/SystemHealth";
import { VisionPage } from "@/components/VisionPage";

const BASE = {
  background: "#050505",
  color: "#00ff88",
  fontFamily: '"JetBrains Mono", "Fira Code", "Courier New", monospace',
  fontSize: 12,
};

export default function Dashboard() {
  const [view, setView] = useState<"dashboard" | "vision">("dashboard");

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", ...BASE, overflow: "hidden" }}>
      <WsInit />
      <DemoDataInjector />

      {/* View toggle */}
      <div style={{
        display: "flex",
        gap: 1,
        background: "#0a0a0a",
        borderBottom: "1px solid #1a1a1a",
        padding: "4px 8px",
        flexShrink: 0,
      }}>
        {(["dashboard", "vision"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            style={{
              background: view === v ? "#00ff88" : "transparent",
              color: view === v ? "#050505" : "#00ff8888",
              border: "1px solid",
              borderColor: view === v ? "#00ff88" : "#1a1a1a",
              padding: "3px 14px",
              fontFamily: "inherit",
              fontSize: 11,
              fontWeight: 700,
              cursor: "pointer",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            {v === "dashboard" ? "▶ Live" : "◈ Mission"}
          </button>
        ))}
      </div>

      {view === "dashboard" ? (
        <>
          <PnLHeader />
          <StrategyStatusBar />
          <div style={{
            flex: 1,
            display: "grid",
            gridTemplateRows: "1.5fr 1fr 0.85fr",
            gridTemplateColumns: "1fr 1.4fr 1fr",
            gap: 1,
            background: "#111",
            overflow: "hidden",
          }}>
            <div style={{ background: "#050505", overflow: "hidden", gridColumn: 1, gridRow: 1 }}>
              <PositionsPanel />
            </div>
            <div style={{ background: "#050505", gridColumn: 2, gridRow: "1 / 3", overflow: "hidden" }}>
              <MicrostructureMesh />
            </div>
            <div style={{ background: "#050505", overflow: "hidden", gridColumn: 3, gridRow: 1 }}>
              <OrderbookDepth />
            </div>
            <div style={{ background: "#050505", overflow: "hidden", gridColumn: 1, gridRow: 2 }}>
              <TradeFeed />
            </div>
            <div style={{ background: "#050505", overflow: "hidden", gridColumn: 3, gridRow: 2 }}>
              <PriceSparkline />
            </div>
            <div style={{ background: "#050505", gridColumn: "1 / 3", gridRow: 3, overflow: "hidden" }}>
              <PerformanceMatrix />
            </div>
            <div style={{ background: "#050505", overflow: "hidden", gridColumn: 3, gridRow: 3 }}>
              <SystemHealth />
            </div>
          </div>
        </>
      ) : (
        <VisionPage />
      )}
    </div>
  );
}
