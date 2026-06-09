import { create } from "zustand";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Trade {
  id: string;
  market_id: string;
  strategy: string;
  side: string;
  entry_price: number;
  fair_value: number;
  edge: number;
  dollar_size: number;
  pnl: number | null;
  paper: boolean;
  timestamp: string;
  btc_open?: number | null;
  btc_settle?: number | null;
  btc_delta_pct?: number | null;
}

export interface Position {
  market_id: string;
  side: string;
  shares: number;
  cost_basis: number;
  current_value: number;
  unrealized_pnl: number;
}

export interface PnL {
  total: number;
  today: number;
  bankroll: number;
  peak: number;
  drawdown_pct: number;
}

export interface BookLevel {
  price: string;
  size: string;
}

export interface Book {
  market_id: string;
  yes_bid: number;
  yes_ask: number;
  no_bid: number;
  no_ask: number;
  seconds_remaining: number;
}

export interface Health {
  ws_binance: boolean;
  ws_clob: boolean;
  open_positions: number;
  strategy_phase: string;
  btc_price: number;
}

// ── Stores ────────────────────────────────────────────────────────────────────

const MAX_TRADES = 200;

export const usePnlStore = create<{
  pnl: PnL;
  history: { t: number; value: number }[];
  update: (data: PnL) => void;
}>((set) => ({
  pnl: { total: 0, today: 0, bankroll: 0, peak: 0, drawdown_pct: 0 },
  history: [],
  update: (data) =>
    set((s) => ({
      pnl: data,
      history: [
        ...s.history.slice(-300),
        { t: Date.now(), value: data.bankroll },
      ],
    })),
}));

export const useTradesStore = create<{
  trades: Trade[];
  addTrade: (t: Trade) => void;
}>((set) => ({
  trades: [],
  addTrade: (t) =>
    set((s) => {
      const idx = s.trades.findIndex((tr) => tr.id === t.id);
      if (idx >= 0) {
        // Resolution event for an existing trade: update pnl only, keep original data
        if (t.pnl !== null) {
          const updated = [...s.trades];
          updated[idx] = { ...updated[idx], pnl: t.pnl };
          return { trades: updated };
        }
        // Duplicate open event — ignore
        return s;
      }
      return { trades: [t, ...s.trades].slice(0, MAX_TRADES) };
    }),
}));

export const usePositionsStore = create<{
  positions: Record<string, Position>;
  update: (p: Position) => void;
  sync: (marketIds: string[]) => void;
  clear: () => void;
}>((set) => ({
  positions: {},
  update: (p) =>
    set((s) => ({ positions: { ...s.positions, [p.market_id]: p } })),
  sync: (marketIds) =>
    set((s) => {
      const keep = new Set(marketIds);
      const next = Object.fromEntries(
        Object.entries(s.positions).filter(([k]) => keep.has(k)),
      );
      // Only trigger a re-render if something actually changed
      return Object.keys(next).length === Object.keys(s.positions).length
        ? s
        : { positions: next };
    }),
  clear: () => set({ positions: {} }),
}));

export const useBookStore = create<{
  book: Book | null;
  update: (b: Book) => void;
  tick: (data: { market_id: string; seconds_remaining: number }) => void;
}>((set) => ({
  book: null,
  update: (b) => set({ book: b }),
  tick: (data) =>
    set((s) => ({
      book: s.book
        ? { ...s.book, seconds_remaining: data.seconds_remaining }
        : {
            // Placeholder values (ActiveMarket defaults) — overwritten once
            // the bridge sends a real book event. Safe because the zeros bug
            // in clob_ws.py is fixed — 0.82/0.85 won't be overwritten with 0.
            market_id: data.market_id,
            yes_bid: 0.820,
            yes_ask: 0.850,
            no_bid: 0.820,
            no_ask: 0.850,
            seconds_remaining: data.seconds_remaining,
          },
    })),
}));

export const useHealthStore = create<{
  health: Health;
  btcHistory: { time: number; value: number }[];
  update: (h: Partial<Health>) => void;
}>((set) => ({
  health: {
    ws_binance: false,
    ws_clob: false,
    open_positions: 0,
    strategy_phase: "SCAN",
    btc_price: 0,
  },
  btcHistory: [],
  update: (h: Partial<Health>) =>
    set((s) => ({
      health: { ...s.health, ...h },
      btcHistory:
        (h.btc_price ?? 0) > 0
          ? [
              ...s.btcHistory.slice(-300),
              { time: Math.floor(Date.now() / 1000), value: h.btc_price! },
            ]
          : s.btcHistory,
    })),
}));
