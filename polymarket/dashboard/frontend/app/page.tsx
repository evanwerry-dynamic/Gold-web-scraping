import { WsInit } from "@/components/WsInit";
import { PnLHeader } from "@/components/PnLHeader";
import { StrategyStatusBar } from "@/components/StrategyStatusBar";
import { SystemHealth } from "@/components/SystemHealth";
import { TradeFeed } from "@/components/TradeFeed";
import { OrderbookDepth } from "@/components/OrderbookDepth";
import { MicrostructureMesh } from "@/components/MicrostructureMesh";
import { PerformanceMatrix } from "@/components/PerformanceMatrix";

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

      {/* Top: P&L header */}
      <PnLHeader />

      {/* Strategy phase pipeline */}
      <StrategyStatusBar />

      {/* Main grid — 3 rows */}
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateRows: "1fr 1fr",
          gridTemplateColumns: "1fr 1.4fr 1fr",
          gap: 1,
          background: "#111",
          overflow: "hidden",
        }}
      >
        {/* Row 1 Col 1: Orderbook */}
        <div style={{ background: "#050505", overflow: "hidden" }}>
          <OrderbookDepth />
        </div>

        {/* Row 1 Col 2: Microstructure mesh (spans both rows) */}
        <div
          style={{
            background: "#050505",
            gridRow: "1 / 3",
            overflow: "hidden",
          }}
        >
          <MicrostructureMesh />
        </div>

        {/* Row 1 Col 3: System health */}
        <div style={{ background: "#050505", overflow: "hidden" }}>
          <SystemHealth />
        </div>

        {/* Row 2 Col 1: Trade feed */}
        <div style={{ background: "#050505", overflow: "hidden" }}>
          <TradeFeed />
        </div>

        {/* Row 2 Col 3: Performance matrix */}
        <div style={{ background: "#050505", overflow: "hidden" }}>
          <PerformanceMatrix />
        </div>
      </div>
    </div>
  );
}
