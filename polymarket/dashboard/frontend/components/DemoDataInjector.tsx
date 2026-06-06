"use client";
import { useEffect, useRef } from "react";
import { usePnlStore, useTradesStore, useHealthStore, useBookStore } from "@/store";

/**
 * Injects simulated bot data when no real WebSocket is connected.
 * Activates 4 seconds after mount if ws_binance is still false.
 * Stops automatically if a real connection later establishes.
 */
export function DemoDataInjector() {
  const startedRef = useRef(false);

  useEffect(() => {
    const init = setTimeout(() => {
      if (useHealthStore.getState().health.ws_binance) return;
      if (startedRef.current) return;
      startedRef.current = true;
      runDemo();
    }, 4000);
    return () => clearTimeout(init);
  }, []);

  return null;
}

function runDemo() {
  let btcPrice = 105800 + Math.random() * 2400;
  let bankroll = 500;
  let totalPnl = 0;
  let todayPnl = 0;
  let tradeCount = 0;

  const PHASES = ["SCAN", "SCAN", "FAIR", "EDGE", "LIMIT", "FILL", "HOLD", "SCAN"] as const;

  const tick = setInterval(() => {
    if (useHealthStore.getState().health.ws_binance) {
      clearInterval(tick);
      return;
    }

    btcPrice *= 1 + (Math.random() - 0.499) * 0.0009;
    btcPrice = Math.max(95000, Math.min(125000, btcPrice));

    const phase = PHASES[Math.floor(Date.now() / 6000) % PHASES.length];

    useHealthStore.getState().update({
      ws_binance: false,
      ws_clob: false,
      open_positions: tradeCount % 3 === 0 ? 1 : 0,
      strategy_phase: phase,
      btc_price: Math.round(btcPrice * 100) / 100,
    });

    const raw = 0.50 + Math.sin(Date.now() / 14000) * 0.33 + (Math.random() - 0.5) * 0.04;
    const mid = Math.max(0.06, Math.min(0.94, raw));
    const windowTs = Math.floor(Date.now() / 300000) * 300;
    const secsLeft = 300 - (Math.floor(Date.now() / 1000) % 300);

    useBookStore.getState().update({
      market_id: `btc-updown-5m-${windowTs}`,
      yes_bid:  parseFloat((mid - 0.006).toFixed(3)),
      yes_ask:  parseFloat((mid + 0.006).toFixed(3)),
      no_bid:   parseFloat((1 - mid - 0.006).toFixed(3)),
      no_ask:   parseFloat((1 - mid + 0.006).toFixed(3)),
      seconds_remaining: secsLeft,
    });

    usePnlStore.getState().update({
      total:        parseFloat(totalPnl.toFixed(2)),
      today:        parseFloat(todayPnl.toFixed(2)),
      bankroll:     parseFloat(bankroll.toFixed(2)),
      peak:         parseFloat(Math.max(500, bankroll).toFixed(2)),
      drawdown_pct: parseFloat(Math.max(0, (500 - bankroll) / 500 * 100).toFixed(2)),
    });
  }, 1000);

  // Fake trade every 15–35 s
  const tradeFn = () => {
    if (useHealthStore.getState().health.ws_binance) return;

    const won      = Math.random() > 0.31;
    const size     = 10 + Math.random() * 55;
    const pnl      = won
      ? +(size * (0.05 + Math.random() * 0.18)).toFixed(2)
      : -(size * (0.03 + Math.random() * 0.09)).toFixed(2);

    totalPnl += pnl;
    todayPnl += pnl;
    bankroll  += pnl;
    tradeCount++;

    const strategies = ["A", "B", "C"] as const;
    const entry = parseFloat((0.70 + Math.random() * 0.24).toFixed(3));

    useTradesStore.getState().addTrade({
      id:          `demo-${tradeCount}-${Date.now()}`,
      market_id:   `btc-updown-5m-${Math.floor(Date.now() / 300000) * 300}`,
      strategy:    strategies[tradeCount % 3],
      side:        "YES",
      entry_price: entry,
      fair_value:  parseFloat((entry + 0.07 + Math.random() * 0.12).toFixed(3)),
      edge:        parseFloat((0.04 + Math.random() * 0.11).toFixed(3)),
      dollar_size: parseFloat(size.toFixed(2)),
      pnl,
      paper:       true,
      timestamp:   new Date().toISOString(),
    });

    setTimeout(tradeFn, 15000 + Math.random() * 20000);
  };

  setTimeout(tradeFn, 8000);
}
