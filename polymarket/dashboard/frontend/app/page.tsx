import { WsInit } from "@/components/WsInit";
import { PnLHeader } from "@/components/PnLHeader";
import { StrategyStatusBar } from "@/components/StrategyStatusBar";
import { PositionsPanel } from "@/components/PositionsPanel";
import { MicrostructureMesh } from "@/components/MicrostructureMesh";
import { OrderbookDepth } from "@/components/OrderbookDepth";
import { TradeFeed } from "@/components/TradeFeed";
import { PriceSparkline } from "@/components/PriceSparkline";
import { PerformanceMatrix } from "@/components/PerformanceMatrix";
import { SystemHealth } from "@/components/SystemHealth";

export default function Dashboard() {
  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: "#050505",
        color: "#00ff88",
        fontFamily: '"JetBrains Mono", "Fira Code", "Courier New", monospace',
        fontSize: 12,
        overflow: "hidden",
      }}
    >
      {/* WebSocket connection (invisible) */}
      <WsInit />

      {/* Top bar: P&L + strategy pipeline */}
      <PnLHeader />
      <StrategyStatusBar />

      {/*
        Main grid — 3 rows × 3 cols
        Row 1: Positions | Mesh (spans rows 1-2) | Orderbook
        Row 2: Trade feed | (mesh) | BTC sparkline
        Row 3: Performance matrix (col 1-2) | System health
      */}
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateRows: "1.5fr 1fr 0.85fr",
          gridTemplateColumns: "1fr 1.4fr 1fr",
          gap: 1,
          background: "#111",
          overflow: "hidden",
        }}
      >
        {/* Row 1, Col 1: Open positions */}
        <div style={{ background: "#050505", overflow: "hidden" }}>
          <PositionsPanel />
        </div>

        {/* Rows 1-2, Col 2: Microstructure mesh */}
        <div
          style={{
            background: "#050505",
            gridRow: "1 / 3",
            overflow: "hidden",
          }}
        >
          <MicrostructureMesh />
        </div>

        {/* Row 1, Col 3: Orderbook */}
        <div style={{ background: "#050505", overflow: "hidden" }}>
          <OrderbookDepth />
        </div>

        {/* Row 2, Col 1: Trade feed */}
        <div style={{ background: "#050505", overflow: "hidden" }}>
          <TradeFeed />
        </div>

        {/* Row 2, Col 3: BTC price sparkline */}
        <div style={{ background: "#050505", overflow: "hidden" }}>
          <PriceSparkline />
        </div>

        {/* Row 3, Col 1-2: Performance matrix */}
        <div
          style={{
            background: "#050505",
            gridColumn: "1 / 3",
            overflow: "hidden",
          }}
        >
          <PerformanceMatrix />
        </div>

        {/* Row 3, Col 3: System health */}
        <div style={{ background: "#050505", overflow: "hidden" }}>
          <SystemHealth />
        </div>
      </div>
    </div>
  );
}
