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
import { RoadmapPage } from "@/components/RoadmapPage";
import { SystemPage } from "@/components/SystemPage";
import { TradeHistoryPage } from "@/components/TradeHistoryPage";

type View = "dashboard" | "vision" | "roadmap" | "system" | "history";

const TABS: { id: View; label: string }[] = [
  { id: "dashboard", label: "▶  Live" },
  { id: "history",   label: "◷  History" },
  { id: "vision",    label: "◈  Mission" },
  { id: "roadmap",   label: "◉  Road Map" },
  { id: "system",    label: "⊞  System" },
];

const BASE = {
  background: "#111",
  color: "#00ff88",
  fontFamily: '"JetBrains Mono", "Fira Code", "Courier New", monospace',
  fontSize: 13,
};

export default function Dashboard() {
  const [view, setView] = useState<View>("dashboard");

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", ...BASE, overflow: "hidden" }}>
      <WsInit />
      <DemoDataInjector />

      {/* Tab bar */}
      <div style={{
        display: "flex",
        gap: 2,
        background: "#080808",
        borderBottom: "1px solid #151515",
        padding: "4px 8px",
        flexShrink: 0,
        alignItems: "center",
      }}>
        {TABS.map((t) => {
          const active = view === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setView(t.id)}
              style={{
                background: active ? "#00ff88" : "transparent",
                color: active ? "#050505" : "#00ff88bb",
                border: "1px solid",
                borderColor: active ? "#00ff88" : "#1c1c1c",
                padding: "3px 16px",
                fontFamily: "inherit",
                fontSize: 12,
                fontWeight: 700,
                cursor: "pointer",
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                transition: "all 0.1s",
                whiteSpace: "nowrap",
              }}
            >
              {t.label}
            </button>
          );
        })}
        <div style={{ flex: 1 }} />
        <div style={{ color: "#bbb", fontSize: 11, letterSpacing: "0.1em" }}>
          MAD SCIENTIST v1.0
        </div>
      </div>

      {/* Views */}
      {view === "dashboard" && (
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
            <div style={{ background: "#111", overflow: "hidden", gridColumn: 1, gridRow: 1 }}>
              <PositionsPanel />
            </div>
            <div style={{ background: "#111", gridColumn: 2, gridRow: "1 / 3", overflow: "hidden" }}>
              <MicrostructureMesh />
            </div>
            <div style={{ background: "#111", overflow: "hidden", gridColumn: 3, gridRow: 1 }}>
              <OrderbookDepth />
            </div>
            <div style={{ background: "#111", overflow: "hidden", gridColumn: 1, gridRow: 2 }}>
              <TradeFeed />
            </div>
            <div style={{ background: "#111", overflow: "hidden", gridColumn: 3, gridRow: 2 }}>
              <PriceSparkline />
            </div>
            <div style={{ background: "#111", gridColumn: "1 / 3", gridRow: 3, overflow: "hidden" }}>
              <PerformanceMatrix />
            </div>
            <div style={{ background: "#111", overflow: "hidden", gridColumn: 3, gridRow: 3 }}>
              <SystemHealth />
            </div>
          </div>
        </>
      )}
      {view === "history"  && <TradeHistoryPage />}
      {view === "vision"   && <VisionPage />}
      {view === "roadmap"  && <RoadmapPage />}
      {view === "system"   && <SystemPage />}
    </div>
  );
}
