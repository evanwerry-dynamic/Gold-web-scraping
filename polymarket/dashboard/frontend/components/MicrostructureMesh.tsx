"use client";
import { useEffect, useRef } from "react";
import { useTradesStore, Trade } from "@/store";

interface Node {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  color: string;
  pnl: number | null;
}

function nodeColor(pnl: number | null, action?: string | null): string {
  if (action === "rejected") return "#ff4466";
  if (pnl === null) return "#4488ff";   // OPEN = blue
  return pnl >= 0 ? "#00ff88" : "#ff4466";
}

export function MicrostructureMesh() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<Node[]>([]);
  const animRef = useRef<number>(0);
  const trades = useTradesStore((s) => s.trades);

  const lastTradeIdRef = useRef<string | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || trades.length === 0) return;

    // canvas.width/height are HTML attributes set by ResizeObserver (async, fires after
    // useEffect). Use getBoundingClientRect() which reads the CSS layout size synchronously.
    const rect = canvas.getBoundingClientRect();
    const w = rect.width || canvas.width || 600;
    const h = rect.height || canvas.height || 400;

    // Tab switch / first mount: nodesRef was reset to []. Bulk-seed all existing trades
    // so the mesh is fully populated immediately without waiting for new arrivals.
    if (nodesRef.current.length === 0) {
      nodesRef.current = trades.slice(0, 80).map((t: Trade) => ({
        id: t.id,
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 1.5,
        vy: (Math.random() - 0.5) * 1.5,
        size: Math.max(4, Math.min(20, Math.abs(t.dollar_size / 10))),
        color: nodeColor(t.pnl, t.action),
        pnl: t.pnl,
      }));
      lastTradeIdRef.current = trades[0].id;
      return;
    }

    // Recolor any node whose trade has resolved (pnl changed from null to a value)
    for (const node of nodesRef.current) {
      const trade = trades.find((t: Trade) => t.id === node.id);
      if (trade && trade.pnl !== null && node.pnl === null) {
        node.pnl = trade.pnl;
        node.color = nodeColor(trade.pnl, trade.action);
      }
    }

    const latest = trades[0];
    if (latest.id === lastTradeIdRef.current) return;
    lastTradeIdRef.current = latest.id;

    nodesRef.current = [
      {
        id: latest.id,
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 1.5,
        vy: (Math.random() - 0.5) * 1.5,
        size: Math.max(4, Math.min(20, Math.abs(latest.dollar_size / 10))),
        color: nodeColor(latest.pnl, latest.action),
        pnl: latest.pnl,
      },
      ...nodesRef.current.slice(0, 80),
    ];
  }, [trades]);

  // Animation loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;

    const animate = () => {
      const { width, height } = canvas;
      ctx.fillStyle = "rgba(5,5,5,0.15)";
      ctx.fillRect(0, 0, width, height);

      const nodes = nodesRef.current;

      // Draw edges between nearby nodes
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 100) {
            ctx.beginPath();
            ctx.strokeStyle = `rgba(80,80,120,${0.3 * (1 - dist / 100)})`;
            ctx.lineWidth = 0.5;
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(nodes[j].x, nodes[j].y);
            ctx.stroke();
          }
        }
      }

      // Draw nodes
      nodes.forEach((n) => {
        // Physics
        n.x += n.vx;
        n.y += n.vy;
        n.vx *= 0.98;
        n.vy *= 0.98;
        if (n.x < 0 || n.x > width) n.vx *= -1;
        if (n.y < 0 || n.y > height) n.vy *= -1;

        // Glow
        const grd = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.size * 2);
        grd.addColorStop(0, n.color + "88");
        grd.addColorStop(1, "transparent");
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.size * 2, 0, Math.PI * 2);
        ctx.fillStyle = grd;
        ctx.fill();

        // Core
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.size, 0, Math.PI * 2);
        ctx.fillStyle = n.color;
        ctx.fill();

        // P&L label (only for resolved trades with significant size)
        const displayPnl = n.pnl ?? 0;
        if (n.pnl !== null && Math.abs(displayPnl) >= 5) {
          ctx.fillStyle = n.color;
          ctx.font = "9px monospace";
          ctx.fillText(
            `${displayPnl >= 0 ? "+" : ""}$${Math.abs(displayPnl).toFixed(0)}`,
            n.x + n.size + 2,
            n.y + 3
          );
        }
      });

      animRef.current = requestAnimationFrame(animate);
    };

    animRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animRef.current);
  }, []);

  // Resize observer
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        canvas.width = e.contentRect.width;
        canvas.height = e.contentRect.height;
      }
    });
    ro.observe(canvas.parentElement!);
    return () => ro.disconnect();
  }, []);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <div style={{ position: "absolute", top: 8, left: 12, color: "#bbb", fontSize: 12, letterSpacing: 1, zIndex: 1 }}>
        MICROSTRUCTURE MESH
      </div>
      <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />
    </div>
  );
}
