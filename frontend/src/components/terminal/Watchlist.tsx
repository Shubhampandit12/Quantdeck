// Watchlist — left panel. Tabs (US Stocks / Crypto), searchable, with live prices
// streamed via openMarketSocket for the currently visible rows.
import { useEffect, useMemo, useRef, useState } from "react";
import "./Watchlist.css";
import { api } from "../../api/client";
import { openMarketSocket } from "../../api/ws";
import { useStore } from "../../state/store";
import type { AssetClass, Quote, SymbolInfo } from "../../api/types";
import { fmtPrice, fmtPct, signClass } from "../../lib/format";

interface Props {
  selected: string;
  onSelect: (sym: string) => void;
}

// How many rows to keep subscribed for live quotes (bounded for perf).
const MAX_LIVE_ROWS = 60;

export default function Watchlist({ selected, onSelect }: Props) {
  const [tab, setTab] = useState<AssetClass>("crypto");
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [query, setQuery] = useState("");
  const quotes = useStore((s) => s.quotes);
  const applyQuote = useStore((s) => s.applyQuote);

  // Load the symbol universe for the active tab.
  useEffect(() => {
    let alive = true;
    api
      .symbols(tab)
      .then((list) => {
        if (alive) setSymbols(list);
      })
      .catch(() => {
        if (alive) setSymbols([]);
      });
    return () => {
      alive = false;
    };
  }, [tab]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q
      ? symbols.filter(
          (s) => s.symbol.toLowerCase().includes(q) || s.name.toLowerCase().includes(q),
        )
      : symbols;
    return list.slice(0, 300);
  }, [symbols, query]);

  // Symbols we subscribe to for live quotes (cap for performance).
  const liveSyms = useMemo(() => filtered.slice(0, MAX_LIVE_ROWS).map((s) => s.symbol), [filtered]);

  // Single market socket; update its subscription set as the visible list changes.
  const sockRef = useRef<ReturnType<typeof openMarketSocket> | null>(null);
  useEffect(() => {
    const sock = openMarketSocket([], (q: Quote) => applyQuote(q));
    sockRef.current = sock;
    return () => {
      sock.close();
      sockRef.current = null;
    };
  }, [applyQuote]);

  useEffect(() => {
    sockRef.current?.setSymbols(liveSyms);
  }, [liveSyms]);

  // Seed prices via REST so rows aren't blank before the first tick.
  useEffect(() => {
    if (!liveSyms.length) return;
    let alive = true;
    api
      .quotes(liveSyms)
      .then((qs) => {
        if (alive) qs.forEach((q) => applyQuote(q));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [liveSyms, applyQuote]);

  return (
    <div className="watchlist">
      <div className="wl-tabs">
        <button className={tab === "us_equity" ? "active" : ""} onClick={() => setTab("us_equity")}>
          US Stocks
        </button>
        <button className={tab === "crypto" ? "active" : ""} onClick={() => setTab("crypto")}>
          Crypto
        </button>
      </div>

      <div className="wl-search">
        <input
          className="input"
          placeholder="Filter…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="wl-header">
        <span>Symbol</span>
        <span style={{ textAlign: "right" }}>Price</span>
        <span style={{ textAlign: "right" }}>Chg%</span>
      </div>

      <div className="wl-rows">
        {filtered.length === 0 && <div className="wl-empty">No symbols</div>}
        {filtered.map((s) => {
          const q = quotes[s.symbol];
          const chg = q?.change_percent ?? null;
          return (
            <div
              key={s.symbol}
              className={`wl-row ${s.symbol === selected ? "active" : ""}`}
              onClick={() => onSelect(s.symbol)}
              title={s.name}
            >
              <span className="wl-sym">{s.symbol}</span>
              <span className="wl-price mono">{q ? fmtPrice(q.price) : "—"}</span>
              <span className={`wl-chg mono ${signClass(chg)}`}>{fmtPct(chg)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
