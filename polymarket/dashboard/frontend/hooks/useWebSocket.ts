"use client";
import { useEffect, useRef } from "react";
import {
  usePnlStore,
  useTradesStore,
  usePositionsStore,
  useBookStore,
  useHealthStore,
} from "@/store";

function resolveWsUrl(): string {
  if (typeof window === "undefined") return "ws://localhost:8000/ws";

  // Explicit one-time override via URL param
  const param = new URLSearchParams(window.location.search).get("ws");
  if (param) {
    try { localStorage.setItem("wsUrl", param); } catch {}
    return param;
  }

  // Production: always use the baked-in env URL — never let a stale localStorage
  // value (e.g., ws://localhost:8000/ws from a dev session) override it.
  if (process.env.NEXT_PUBLIC_WS_URL) {
    try { localStorage.removeItem("wsUrl"); } catch {}   // evict stale entry
    return process.env.NEXT_PUBLIC_WS_URL;
  }

  // Development fallback: check localStorage, then current host
  try {
    const stored = localStorage.getItem("wsUrl");
    if (stored) return stored;
  } catch {}
  return `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
}

const WS_URL = resolveWsUrl();

let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

function connect() {
  if (socket && socket.readyState <= WebSocket.OPEN) return;

  socket = new WebSocket(WS_URL);

  socket.onmessage = (evt) => {
    let msg: { type: string; data: unknown };
    try {
      msg = JSON.parse(evt.data);
    } catch {
      return;
    }

    // Batch all store updates in a single rAF to avoid layout thrash
    requestAnimationFrame(() => {
      switch (msg.type) {
        case "pnl":
          usePnlStore.getState().update(msg.data as never);
          break;
        case "trade":
          useTradesStore.getState().addTrade(msg.data as never);
          break;
        case "position":
          usePositionsStore.getState().update(msg.data as never);
          break;
        case "positions_sync":
          usePositionsStore.getState().sync(
            (msg.data as { market_ids: string[] }).market_ids,
          );
          break;
        case "book":
          useBookStore.getState().update(msg.data as never);
          break;
        case "tick":
          useBookStore.getState().tick(msg.data as { market_id: string; seconds_remaining: number });
          break;
        case "health":
          useHealthStore.getState().update(msg.data as never);
          break;
      }
    });
  };

  socket.onopen = () => {
    // Clear stale state on every (re)connect — bot will re-broadcast current
    // state. Reset health indicators so stale green never persists across reconnects.
    usePositionsStore.getState().clear();
    useHealthStore.getState().update({ ws_binance: false, ws_clob: false });
  };

  socket.onclose = () => {
    reconnectTimer = setTimeout(connect, 3000);
  };

  socket.onerror = () => {
    socket?.close();
  };
}

/** Hook: call once at the root layout level to establish the WS connection. */
export function useWebSocket() {
  const mounted = useRef(false);
  useEffect(() => {
    if (mounted.current) return;
    mounted.current = true;
    connect();
    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, []);
}
