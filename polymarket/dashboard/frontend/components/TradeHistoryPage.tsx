"use client";
import ReactECharts from "echarts-for-react";
import { useTradesStore, usePnlStore, Trade } from "@/store";

const STRAT: Record<string, string> = {
  A: "Momentum",
  B: "Market Make",
  C: "Arbitrage",
};

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-GB", {
      month: "short", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
  } catch {
    return iso.slice(0, 16).replace("T", " ");
  }
}

function StatCard({
  label, value, sub, color,
}: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div style={{
      flex: "1 1 130px",
      background: "#111",
      border: "1px solid #222",
      padding: "12px 14px",
    }}>
      <div style={{ color: "#666", fontSize: 9, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ color: color ?? "#00ff88", fontSize: 20, fontWeight: 700, lineHeight: 1 }}>
        {value}
      </div>
      {sub && <div style={{ color: "#555", fontSize: 10, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

type StratBucket = { trades: number; pnl: number; wins: number; volume: number };

export function TradeHistoryPage() {
  const trades = useTradesStore((s) => s.trades);
  // Server-authoritative P&L — same source as Live page header.
  // Avoids drift when the 200-record replay window doesn't cover all-time history.
  const serverPnl = usePnlStore((s) => s.pnl);

  // ── Metrics ──────────────────────────────────────────────────────────────────
  const closed = trades.filter((t) => t.pnl !== null);
  const wins   = closed.filter((t) => (t.pnl ?? 0) > 0);
  const winRate   = closed.length > 0 ? wins.length / closed.length : null;
  const avgEdge   = trades.length > 0 ? trades.reduce((s, t) => s + (t.edge ?? 0), 0) / trades.length : 0;
  const bestTrade  = closed.length > 0 ? Math.max(...closed.map((t) => t.pnl ?? 0)) : null;
  const worstTrade = closed.length > 0 ? Math.min(...closed.map((t) => t.pnl ?? 0)) : null;
  const totalVolume = trades.reduce((s, t) => s + (t.dollar_size ?? 0), 0);

  // ── Cumulative P&L series ────────────────────────────────────────────────────
  const sorted = [...trades].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );
  let cum = 0;
  const cumData: [string, number][] = sorted.map((t) => {
    cum += t.pnl ?? 0;
    return [t.timestamp, parseFloat(cum.toFixed(2))];
  });

  // ── Strategy breakdown ───────────────────────────────────────────────────────
  const buckets: Record<string, StratBucket> = {};
  for (const t of trades) {
    const k = t.strategy ?? "?";
    if (!buckets[k]) buckets[k] = { trades: 0, pnl: 0, wins: 0, volume: 0 };
    buckets[k].trades++;
    buckets[k].pnl += t.pnl ?? 0;
    buckets[k].volume += t.dollar_size ?? 0;
    if ((t.pnl ?? 0) > 0) buckets[k].wins++;
  }
  const stratKeys   = Object.keys(buckets);
  const stratLabels = stratKeys.map((k) => STRAT[k] ?? k);
  const stratPnl    = stratKeys.map((k) => parseFloat(buckets[k].pnl.toFixed(2)));

  // ── ECharts options ──────────────────────────────────────────────────────────
  const pnlOption = {
    backgroundColor: "transparent",
    grid: { top: 16, right: 16, bottom: 36, left: 56 },
    xAxis: {
      type: "time",
      axisLabel: { color: "#666", fontSize: 9 },
      axisLine: { lineStyle: { color: "#222" } },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#444", fontSize: 9, formatter: (v: number) => `$${v}` },
      splitLine: { lineStyle: { color: "#1a1a1a" } },
    },
    series: [{
      type: "line",
      data: cumData,
      smooth: 0.3,
      lineStyle: { color: totalPnl >= 0 ? "#00ff88" : "#ff4444", width: 2 },
      areaStyle: { color: totalPnl >= 0 ? "rgba(0,255,136,0.07)" : "rgba(255,68,68,0.07)" },
      symbol: "none",
    }],
    tooltip: {
      trigger: "axis",
      backgroundColor: "#0d0d0d",
      borderColor: "#222",
      textStyle: { color: "#00ff88", fontSize: 11, fontFamily: "monospace" },
      formatter: (params: { value: [string, number] }[]) =>
        `${fmtTime(params[0].value[0])}<br/>P&L: <b>$${params[0].value[1].toFixed(2)}</b>`,
    },
  };

  const stratOption = {
    backgroundColor: "transparent",
    grid: { top: 16, right: 16, bottom: 36, left: 90 },
    xAxis: {
      type: "value",
      axisLabel: { color: "#444", fontSize: 9, formatter: (v: number) => `$${v}` },
      splitLine: { lineStyle: { color: "#1a1a1a" } },
    },
    yAxis: {
      type: "category",
      data: stratLabels,
      axisLabel: { color: "#888", fontSize: 10 },
      axisLine: { lineStyle: { color: "#222" } },
    },
    series: [{
      type: "bar",
      data: stratPnl.map((v, i) => ({
        value: v,
        itemStyle: { color: v >= 0 ? "#00ff88" : "#ff4444", borderRadius: [0, 2, 2, 0] },
        label: {
          show: true,
          position: v >= 0 ? "right" : "left",
          color: "#555",
          fontSize: 9,
          formatter: `${buckets[stratKeys[i]].trades}t`,
        },
      })),
      barMaxWidth: 28,
    }],
    tooltip: {
      backgroundColor: "#0d0d0d",
      borderColor: "#222",
      textStyle: { color: "#00ff88", fontSize: 11, fontFamily: "monospace" },
      formatter: (p: { dataIndex: number }) => {
        const k = stratKeys[p.dataIndex];
        const b = buckets[k];
        const wr = b.trades > 0 ? ((b.wins / b.trades) * 100).toFixed(0) : 0;
        return `${STRAT[k] ?? k}<br/>Trades: ${b.trades}  Win: ${wr}%<br/>P&L: $${b.pnl.toFixed(2)}<br/>Volume: $${b.volume.toFixed(0)}`;
      },
    },
  };

  // ── Sorted display list (newest first) ───────────────────────────────────────
  const displayTrades = [...trades].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  );

  // BTC delta stats (only for resolved trades that have btc_delta_pct)
  const withDelta = closed.filter((t) => t.btc_delta_pct != null);
  const deltaMatchCount = withDelta.filter((t) => {
    const up = (t.btc_delta_pct ?? 0) >= 0;
    return (["UP","YES"].includes(t.side) && up) || (!["UP","YES"].includes(t.side) && !up);
  }).length;
  const verifiedWinRate = withDelta.length > 0 ? deltaMatchCount / withDelta.length : null;

  const COL = "110px 90px 75px 50px 75px 60px 55px 55px 70px";

  return (
    <div style={{ flex: 1, overflowY: "auto", background: "#0a0a0a", padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>

      {/* ── Stat cards ── */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <StatCard label="Total Trades"  value={String(trades.length)}
          sub={`${closed.length} closed`} />
        <StatCard label="Win Rate"
          value={winRate !== null ? `${(winRate * 100).toFixed(1)}%` : "—"}
          sub={`${wins.length} / ${closed.length}`}
          color={winRate === null ? "#555" : winRate >= 0.6 ? "#00ff88" : winRate >= 0.4 ? "#ffcc00" : "#ff4444"} />
        <StatCard label="Verified"
          value={verifiedWinRate !== null ? `${(verifiedWinRate * 100).toFixed(1)}%` : "—"}
          sub={`${withDelta.length} auditable`}
          color={verifiedWinRate === null ? "#555" : verifiedWinRate >= 0.6 ? "#00ff88" : "#ff4444"} />
        <StatCard label="Total P&L"
          value={serverPnl.bankroll > 0 ? `$${serverPnl.total.toFixed(2)}` : "—"}
          sub="server total"
          color={serverPnl.total >= 0 ? "#00ff88" : "#ff4444"} />
        <StatCard label="Avg Edge"
          value={`${(avgEdge * 100).toFixed(2)}%`}
          color={avgEdge >= 0 ? "#00ff88" : "#ff4444"} />
        <StatCard label="Best Trade"
          value={bestTrade !== null ? `$${bestTrade.toFixed(2)}` : "—"}
          color="#00ff88" />
        <StatCard label="Worst Trade"
          value={worstTrade !== null ? `$${worstTrade.toFixed(2)}` : "—"}
          color={worstTrade !== null && worstTrade < 0 ? "#ff4444" : "#555"} />
        <StatCard label="Volume"
          value={`$${totalVolume.toFixed(0)}`}
          color="#888" />
      </div>

      {/* ── Charts ── */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 10, minHeight: 220 }}>
        <div style={{ background: "#111", border: "1px solid #222", padding: "10px 12px" }}>
          <div style={{ color: "#666", fontSize: 9, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 6 }}>
            Cumulative P&L
          </div>
          {cumData.length > 1 ? (
            <ReactECharts option={pnlOption} style={{ height: 180 }} opts={{ renderer: "canvas" }} />
          ) : (
            <div style={{ height: 180, display: "flex", alignItems: "center", justifyContent: "center", color: "#333", fontSize: 12 }}>
              Waiting for trades
            </div>
          )}
        </div>
        <div style={{ background: "#111", border: "1px solid #222", padding: "10px 12px" }}>
          <div style={{ color: "#666", fontSize: 9, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 6 }}>
            P&L by Strategy
          </div>
          {stratKeys.length > 0 ? (
            <ReactECharts option={stratOption} style={{ height: 180 }} opts={{ renderer: "canvas" }} />
          ) : (
            <div style={{ height: 180, display: "flex", alignItems: "center", justifyContent: "center", color: "#333", fontSize: 12 }}>
              Waiting for trades
            </div>
          )}
        </div>
      </div>

      {/* ── Trade table ── */}
      <div style={{ background: "#111", border: "1px solid #222", overflowX: "auto" }}>
        {/* Header */}
        <div style={{
          display: "grid", gridTemplateColumns: COL, gap: 6,
          padding: "7px 12px", borderBottom: "1px solid #1e1e1e",
          color: "#555", fontSize: 9, letterSpacing: "0.12em", textTransform: "uppercase",
        }}>
          <span>Time</span><span>Market</span><span>Strategy</span>
          <span>Side</span><span>BTC Δ</span><span>Entry</span>
          <span>Edge</span><span>Size</span><span style={{ textAlign: "right" }}>P&L</span>
        </div>

        {displayTrades.length === 0 ? (
          <div style={{ padding: 32, textAlign: "center", color: "#333", fontSize: 12 }}>
            No trades yet — strategy fires at T−10s when |Δ| &gt; 0.10%
          </div>
        ) : (
          displayTrades.map((t: Trade) => {
            const pnlColor = t.pnl === null ? "#444"
              : t.pnl > 0 ? "#00ff88" : "#ff4444";
            const sideColor = ["UP", "YES"].includes(t.side) ? "#00ff88" : "#ff9900";

            // BTC Δ column: show window move % + auditable indicator
            const hasDelta = t.btc_delta_pct != null;
            const deltaPositive = (t.btc_delta_pct ?? 0) >= 0;
            const betUp = ["UP","YES"].includes(t.side);
            // Verified = direction matches the bet (if trade is closed)
            const verified = t.pnl !== null && hasDelta && ((betUp && deltaPositive) || (!betUp && !deltaPositive));
            const deltaColor = !hasDelta ? "#444" : deltaPositive ? "#00ff88" : "#ff4444";
            const deltaTooltip = (t.btc_open && t.btc_settle)
              ? `BTC: ${t.btc_open.toLocaleString("en-US", {maximumFractionDigits: 0})} → ${t.btc_settle.toLocaleString("en-US", {maximumFractionDigits: 0})}`
              : undefined;

            return (
              <div key={t.id} style={{
                display: "grid", gridTemplateColumns: COL, gap: 6,
                padding: "5px 12px", borderBottom: "1px solid #161616",
                fontSize: 11, alignItems: "center",
              }}>
                <span style={{ color: "#777" }}>{fmtTime(t.timestamp)}</span>
                <span style={{ color: "#888", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 10 }}>
                  {t.market_id}
                </span>
                <span style={{ color: "#aaa" }}>{STRAT[t.strategy] ?? t.strategy}</span>
                <span style={{ color: sideColor, fontWeight: 700 }}>{t.side}</span>
                <span
                  style={{ color: deltaColor, fontFamily: "monospace", fontSize: 10 }}
                  title={deltaTooltip}
                >
                  {hasDelta
                    ? `${deltaPositive ? "+" : ""}${(t.btc_delta_pct ?? 0).toFixed(3)}%${t.pnl !== null ? (verified ? " ✓" : " ✗") : ""}`
                    : "—"}
                </span>
                <span>{(t.entry_price ?? 0).toFixed(3)}</span>
                <span style={{ color: "#4ec994" }}>{((t.edge ?? 0) * 100).toFixed(1)}%</span>
                <span style={{ color: "#aaa" }}>${(t.dollar_size ?? 0).toFixed(2)}</span>
                <span style={{ textAlign: "right", color: pnlColor, fontWeight: 700 }}>
                  {t.pnl === null ? <span style={{ color: "#555", fontWeight: 400 }}>OPEN</span> : `$${t.pnl.toFixed(2)}`}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
