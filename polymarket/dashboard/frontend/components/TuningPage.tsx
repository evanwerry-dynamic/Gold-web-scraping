"use client";
import { useEffect, useState } from "react";

type Ranges = Record<string, [number, number]>;
type Params = Record<string, number>;

const LEVERS: {
  key: string;
  label: string;
  unit: string;
  effect: string;
  fmt: (v: number) => string;
}[] = [
  {
    key: "min_edge_net",
    label: "Min Net Edge",
    unit: "$ per share",
    effect: "Higher = fewer, higher-conviction trades. The main win-rate lever. 0.07 = 7¢ edge required after fees. Below 0.02 erodes the safety margin against model error — keep ≥0.02 until the model is proven.",
    fmt: (v) => `${(v * 100).toFixed(1)}¢`,
  },
  {
    key: "min_z_score",
    label: "Conviction (z-score)",
    unit: "min |z| = δ / (σ·√T)",
    effect: "Strategy A conviction gate — scales the required move with volatility. 0.674≈fair 0.75, 1.04≈fair 0.85, 0=disabled. Lower this to let more Strategy A signals through; raise to demand more decisive moves.",
    fmt: (v) => v.toFixed(2),
  },
  {
    key: "min_delta_threshold",
    label: "Min BTC Move",
    unit: "fraction (0.001 = 0.10%)",
    effect: "Higher = only trade decisive BTC moves. Raise toward 0.002 if win rate drifts down.",
    fmt: (v) => `${(v * 100).toFixed(3)}%`,
  },
  {
    key: "entry_seconds_before_close",
    label: "Entry Window",
    unit: "seconds before close",
    effect: "How early before window close the strategy may fire. Lower = more certainty, thinner books.",
    fmt: (v) => `${v.toFixed(0)}s`,
  },
  {
    key: "min_order_size_usd",
    label: "Min Order Size",
    unit: "USD",
    effect: "Orders sized below this are skipped. 0 = pipeline-test mode. Set 5 when scaling capital.",
    fmt: (v) => `$${v.toFixed(2)}`,
  },
  {
    key: "kelly_max_pct",
    label: "Kelly Cap",
    unit: "fraction of bankroll",
    effect: "Hard cap per trade. At small bankroll set high enough that Kelly × bankroll ≥ $1 (Polymarket minimum). E.g. 0.15 at $10 = $1.50/trade. Tighten toward 0.03 once bankroll exceeds ~$100.",
    fmt: (v) => `${(v * 100).toFixed(1)}%`,
  },
  {
    key: "maker_quote_pct",
    label: "Maker Quote Size",
    unit: "fraction of bankroll per side",
    effect: "Strategy B quote size per side. 0.06 = 6% (~$1.14 at $19). Each side must clear Min Order Size or Strategy B goes dormant.",
    fmt: (v) => `${(v * 100).toFixed(1)}%`,
  },
  {
    key: "maker_half_spread",
    label: "Maker Half-Spread",
    unit: "$ per share each side",
    effect: "Strategy B quote distance from the reservation price. Wider = safer fills but fewer trades; tighter = more fills but more adverse selection.",
    fmt: (v) => `${(v * 100).toFixed(1)}¢`,
  },
  {
    key: "maker_inventory_skew",
    label: "Maker Inventory Skew",
    unit: "fraction",
    effect: "How aggressively Strategy B shades quotes to offload inventory. Higher = faster mean-reversion to flat, lower = holds inventory longer.",
    fmt: (v) => v.toFixed(3),
  },
  {
    key: "maker_max_sigma",
    label: "Maker Max Vol",
    unit: "σ per second",
    effect: "Strategy B pulls all quotes when realized vol exceeds this (trending market → adverse selection). Higher = quote through choppier tape; lower = only quote in calm markets.",
    fmt: (v) => v.toFixed(5),
  },
  {
    key: "maker_fair_band",
    label: "Maker Fair Band",
    unit: "± around 0.50",
    effect: "Strategy B only quotes when fair value sits within 0.5±band (near coin-flip). Higher = quote in more directional markets; lower = only quote true 50/50 markets.",
    fmt: (v) => `±${v.toFixed(2)}`,
  },
];

export function TuningPage() {
  const [params, setParams] = useState<Params | null>(null);
  const [ranges, setRanges] = useState<Ranges>({});
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<{ ok: boolean; msg: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const r = await fetch("/params");
      const j = await r.json();
      setParams(j.params);
      setRanges(j.ranges);
      setEdits({});
    } catch {
      setStatus({ ok: false, msg: "Cannot reach bot — is the backend running?" });
    }
  };

  useEffect(() => {
    load();
  }, []);

  const apply = async () => {
    const body: Record<string, number> = {};
    for (const [k, v] of Object.entries(edits)) {
      if (v.trim() !== "" && params && parseFloat(v) !== params[k]) {
        body[k] = parseFloat(v);
      }
    }
    if (Object.keys(body).length === 0) {
      setStatus({ ok: false, msg: "Nothing changed" });
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/params", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (r.ok) {
        setParams(j.params);
        setEdits({});
        setStatus({ ok: true, msg: "Applied — live within 2 seconds. Persisted across redeploys." });
      } else {
        setStatus({ ok: false, msg: j.error ?? "Rejected" });
      }
    } catch {
      setStatus({ ok: false, msg: "Request failed — bot unreachable" });
    } finally {
      setBusy(false);
    }
  };

  const dirty = params !== null && Object.entries(edits).some(
    ([k, v]) => v.trim() !== "" && parseFloat(v) !== params[k],
  );

  return (
    <div style={{ flex: 1, overflowY: "auto", background: "#0a0a0a", padding: 16 }}>
      <div style={{ maxWidth: 860, margin: "0 auto", display: "flex", flexDirection: "column", gap: 12 }}>

        <div style={{ color: "#aaa", fontSize: 13, letterSpacing: "0.1em", textTransform: "uppercase" }}>
          Live Strategy Tuning — changes apply on the next 2s signal cycle, no restart
        </div>

        {params === null ? (
          <div style={{ padding: 40, textAlign: "center", color: "#888", fontSize: 14 }}>
            Loading current parameters…
          </div>
        ) : (
          LEVERS.map((l) => {
            const cur = params[l.key];
            const [lo, hi] = ranges[l.key] ?? [0, 0];
            const edited = edits[l.key] ?? "";
            const pending = edited.trim() !== "" && parseFloat(edited) !== cur;
            const outOfRange = pending && (parseFloat(edited) < lo || parseFloat(edited) > hi);
            return (
              <div key={l.key} style={{
                background: "#151515", border: "1px solid #2a2a2a", padding: "14px 16px",
                display: "grid", gridTemplateColumns: "200px 120px 150px 1fr", gap: 14, alignItems: "center",
              }}>
                <div>
                  <div style={{ color: "#00ff88", fontSize: 15, fontWeight: 700 }}>{l.label}</div>
                  <div style={{ color: "#888", fontSize: 11, marginTop: 3 }}>{l.unit}</div>
                </div>
                <div>
                  <div style={{ color: "#aaa", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em" }}>current</div>
                  <div style={{ color: "#fff", fontSize: 18, fontWeight: 700 }}>{l.fmt(cur)}</div>
                  <div style={{ color: "#777", fontSize: 11 }}>{cur}</div>
                </div>
                <div>
                  <input
                    value={edited}
                    placeholder={String(cur)}
                    onChange={(e) => setEdits((s) => ({ ...s, [l.key]: e.target.value }))}
                    style={{
                      width: "100%", background: "#0d0d0d", color: outOfRange ? "#ff4444" : "#00ff88",
                      border: "1px solid", borderColor: outOfRange ? "#ff4444" : pending ? "#00ff88" : "#333",
                      padding: "8px 10px", fontFamily: "inherit", fontSize: 14,
                    }}
                  />
                  <div style={{ color: outOfRange ? "#ff6666" : "#888", fontSize: 11, marginTop: 4 }}>
                    range {lo} – {hi}
                  </div>
                </div>
                <div style={{ color: "#ccc", fontSize: 12, lineHeight: 1.6 }}>{l.effect}</div>
              </div>
            );
          })
        )}

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button
            onClick={apply}
            disabled={!dirty || busy}
            style={{
              background: dirty ? "#00ff88" : "#1a1a1a",
              color: dirty ? "#050505" : "#555",
              border: "none", padding: "10px 32px",
              fontFamily: "inherit", fontSize: 13, fontWeight: 700,
              letterSpacing: "0.1em", textTransform: "uppercase",
              cursor: dirty ? "pointer" : "default",
            }}
          >
            {busy ? "Applying…" : "Apply Changes"}
          </button>
          <button
            onClick={load}
            style={{
              background: "transparent", color: "#aaa", border: "1px solid #333",
              padding: "10px 20px", fontFamily: "inherit", fontSize: 12, cursor: "pointer",
            }}
          >
            Reset / Refresh
          </button>
          {status && (
            <span style={{ color: status.ok ? "#00ff88" : "#ff4444", fontSize: 13 }}>
              {status.msg}
            </span>
          )}
        </div>

        <div style={{ background: "#110d00", border: "1px solid #443800", padding: "12px 16px", color: "#ffdd55", fontSize: 12, lineHeight: 1.7 }}>
          ⚠ Breakeven win rate ≈ your average entry price (+~2% fees/slippage). At 0.86 entries you need ~88%
          settled wins. Min Net Edge is the gate that keeps the model&apos;s target (~93%) above that line —
          lower it with care. Out-of-range values are rejected. The nightly calibrator writes to these same
          parameters; manual changes here persist until it next adjusts them.
        </div>
      </div>
    </div>
  );
}
