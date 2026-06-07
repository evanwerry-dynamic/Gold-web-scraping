"use client";
import { useEffect, useRef } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import type { UTCTimestamp } from "lightweight-charts";
import { useHealthStore } from "@/store";

export function PriceSparkline() {
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const seriesRef = useRef<any>(null);
  const lastTimeRef = useRef<number>(0);

  const btcPrice = useHealthStore((s) => s.health.btc_price);

  // Initialize chart on mount
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: {
        background: { color: "#050505" },
        textColor: "#444",
        fontSize: 12,
      },
      grid: {
        vertLines: { color: "#111" },
        horzLines: { color: "#111" },
      },
      crosshair: { mode: 0 },
      rightPriceScale: {
        borderColor: "#555",
        textColor: "#444",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: "#555",
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: false,
      handleScale: false,
    });

    const series = chart.addSeries(LineSeries, {
      color: "#00ff88",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Feed BTC price ticks
  useEffect(() => {
    if (!seriesRef.current || btcPrice === 0) return;

    // Deduplicate same-second updates (lightweight-charts throws on duplicate time)
    const t = Math.floor(Date.now() / 1000) as UTCTimestamp;
    if (t === lastTimeRef.current) {
      try {
        seriesRef.current.update({ time: t, value: btcPrice });
      } catch {
        // Update on same tick — ignore
      }
      return;
    }

    lastTimeRef.current = t;
    try {
      seriesRef.current.update({ time: t, value: btcPrice });
    } catch {
      // Chart may have been removed
    }
  }, [btcPrice]);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <div
        style={{
          position: "absolute",
          top: 8,
          left: 12,
          color: "#999",
          fontSize: 12,
          letterSpacing: 1,
          zIndex: 1,
          pointerEvents: "none",
        }}
      >
        BTC/USD
      </div>
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
    </div>
  );
}
