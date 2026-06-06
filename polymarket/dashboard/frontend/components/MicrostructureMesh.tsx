"use client";
import { useEffect, useRef } from "react";
import { useTradesStore } from "@/store";

interface Node {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  color: string;
  pnl: number;
}

export function MicrostructureMesh() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<Node[]>([]);
  const animRef = useRef<number>(0);
  const trades = useTradesStore((s) => s.trades);

  // Add new nodes when trades arrive
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || trades.length === 0) return;
    const latest = trades[0];
    const pnl = latest.pnl ?? 0;
    nodesRef.current = [
      {
        id: `${Date.now()}`,
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        vx: (Math.random() - 0.5) * 1.5,
        vy: (Math.random() - 0.5) * 1.5,
        size: Math.max(4, Math.min(20, Math.abs(latest.dollar_size / 10))),
        color: pnl >= 0 ? "#00ff88" : "#ff4466",
        pnl,
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

        // P&L label
        if (Math.abs(n.pnl) >= 5) {
          ctx.fillStyle = n.color;
          ctx.font = "9px monospace";
          ctx.fillText(
            `${n.pnl >= 0 ? "+" : ""}$${Math.abs(n.pnl).toFixed(0)}`,
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
      <div style={{ position: "absolute", top: 8, left: 12, color: "#555", fontSize: 10, letterSpacing: 1, zIndex: 1 }}>
        MICROSTRUCTURE MESH
      </div>
      <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />
    </div>
  );
}
