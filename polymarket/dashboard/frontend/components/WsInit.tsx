"use client";
import { useWebSocket } from "@/hooks/useWebSocket";

/** Invisible component — just establishes the WebSocket connection. */
export function WsInit() {
  useWebSocket();
  return null;
}
