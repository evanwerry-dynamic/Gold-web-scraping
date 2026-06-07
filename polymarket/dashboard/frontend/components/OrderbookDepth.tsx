"use client";
import { useBookStore } from "@/store";

export function OrderbookDepth() {
  const book = useBookStore((s) => s.book);

  if (!book) {
    return (
      <div style={{ padding: 12, color: "#777", fontSize: 11 }}>
        <div style={{ color: "#999", fontSize: 12, letterSpacing: 1, marginBottom: 8 }}>
          ORDERBOOK
        </div>
        Awaiting market data...
      </div>
    );
  }

  const mid = ((book.yes_bid + book.yes_ask) / 2).toFixed(3);
  const spread = (book.yes_ask - book.yes_bid).toFixed(3);

  return (
    <div style={{ padding: 12, fontSize: 11 }}>
      <div style={{ color: "#999", fontSize: 12, letterSpacing: 1, marginBottom: 8 }}>
        ORDERBOOK — {book.market_id?.split("-").slice(-3).join("-")}
      </div>

      {/* Visual depth bars */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
          <span style={{ color: "#00ff88" }}>BID {book.yes_bid.toFixed(3)}</span>
          <span style={{ color: "#ff4466" }}>ASK {book.yes_ask.toFixed(3)}</span>
        </div>
        <div style={{ height: 8, background: "#111", borderRadius: 2, overflow: "hidden", display: "flex" }}>
          <div
            style={{
              width: `${book.yes_bid * 100}%`,
              background: "#00ff8844",
              borderRight: "1px solid #00ff88",
            }}
          />
          <div style={{ flex: 1, background: "#ff446622" }} />
        </div>
      </div>

      {/* Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <div>
          <div style={{ color: "#888", fontSize: 10 }}>MID</div>
          <div style={{ color: "#ccc" }}>{mid}</div>
        </div>
        <div>
          <div style={{ color: "#888", fontSize: 10 }}>SPREAD</div>
          <div style={{ color: "#888" }}>{spread}</div>
        </div>
        <div>
          <div style={{ color: "#888", fontSize: 10 }}>T-REMAINING</div>
          <div style={{ color: book.seconds_remaining < 30 ? "#ff4466" : "#00ff88" }}>
            {book.seconds_remaining.toFixed(0)}s
          </div>
        </div>
        <div>
          <div style={{ color: "#888", fontSize: 10 }}>NO ASK</div>
          <div style={{ color: "#888" }}>{book.no_ask.toFixed(3)}</div>
        </div>
      </div>
    </div>
  );
}
