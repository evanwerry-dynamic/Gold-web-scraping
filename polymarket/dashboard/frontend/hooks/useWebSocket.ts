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
  const param = new URLSearchParams(window.location.search).get("ws");
  if (param) {
    try { localStorage.setItem("wsUrl", param); } catch {}
    return param;
  }
  try {
    const stored = localStorage.getItem("wsUrl");
    if (stored) return stored;
  } catch {}
  if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
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
        case "book":
          useBookStore.getState().update(msg.data as never);
          break;
        case "health":
          useHealthStore.getState().update(msg.data as never);
          break;
      }
    });
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
