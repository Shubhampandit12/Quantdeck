// HomePage — TradingView-style landing page for QuantDeck (paper/demo trading).
// Sections: sticky nav w/ search + demo badge, hero + CTA, live ticker tape,
// and top gainers/losers.
// All data loads defensively: the backend may return 503 for unavailable data,
// so every fetch is wrapped in try/catch and degrades to a subtle placeholder.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { openMarketSocket } from "../api/ws";
import type { Quote, SymbolInfo } from "../api/types";
import { fmtPrice, fmtPct, signClass } from "../lib/format";
import "./HomePage.css";

// Major symbols shown in the ticker tape and watchlist preview.
const TICKER_SYMBOLS = [
  "AAPL",
  "MSFT",
  "NVDA",
  "TSLA",
  "AMZN",
  "GOOGL",
  "META",
  "SPY",
];

// Route to the terminal for a given symbol.
const terminalPath = (symbol: string) => `/terminal/${encodeURIComponent(symbol)}`;

/** Compute a display change% for a quote (prefer explicit change_percent). */
function changePct(q: Quote | undefined): number | null {
  if (!q) return null;
  if (q.change_percent !== null && q.change_percent !== undefined) return q.change_percent;
  if (
    q.prev_close !== null &&
    q.prev_close !== undefined &&
    q.prev_close !== 0 &&
    q.price !== null &&
    q.price !== undefined
  ) {
    return ((q.price - q.prev_close) / q.prev_close) * 100;
  }
  return null;
}

export default function HomePage() {
  const navigate = useNavigate();

  // Live quote cache keyed by symbol, seeded via REST then updated over WS.
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});

  // Top movers from market summary (guarded — may be empty on 503).
  const [moversUp, setMoversUp] = useState<Quote[]>([]);
  const [moversDown, setMoversDown] = useState<Quote[]>([]);
  const [summaryFailed, setSummaryFailed] = useState(false);

  // --- Seed ticker quotes + subscribe to live updates -----------------------
  useEffect(() => {
    let alive = true;
    api
      .quotes(TICKER_SYMBOLS)
      .then((qs) => {
        if (!alive) return;
        const next: Record<string, Quote> = {};
        for (const q of qs) next[q.symbol] = q;
        setQuotes((prev) => ({ ...prev, ...next }));
      })
      .catch(() => {
        /* data unavailable — ticker just renders placeholders */
      });

    const sock = openMarketSocket(TICKER_SYMBOLS, (q) => {
      setQuotes((prev) => ({ ...prev, [q.symbol]: q }));
    });
    return () => {
      alive = false;
      sock.close();
    };
  }, []);

  // --- Load top gainers / losers -------------------------------------------
  useEffect(() => {
    let alive = true;
    api
      .marketSummary()
      .then((s) => {
        if (!alive) return;
        setMoversUp(s.movers_up ?? []);
        setMoversDown(s.movers_down ?? []);
      })
      .catch(() => {
        if (alive) setSummaryFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const goSymbol = useCallback(
    (symbol: string) => navigate(terminalPath(symbol)),
    [navigate],
  );

  // Ticker items in a stable order; duplicated in the track for a seamless loop.
  const tickerItems = useMemo(
    () => TICKER_SYMBOLS.map((sym) => ({ sym, q: quotes[sym] })),
    [quotes],
  );

  return (
    <div className="home">
      <HomeNav />

      <TickerTape items={tickerItems} onSelect={goSymbol} />

      {/* ------------------------------------------------------------- Hero */}
      <header className="home-hero">
        <div className="hero-inner">
          <div className="hero-eyebrow">
            <span className="dot" />
            <span>AI-powered paper trading terminal — no real money at risk</span>
          </div>
          <h1 className="hero-title">
            Trade the markets with <span className="grad">AI</span>
            <br />
            with zero real-money risk
          </h1>
          <p className="hero-sub">
            A full TradingView-style terminal for US stocks. Chart, place
            paper orders, run code backtests, and let an AI agent trade a
            demo portfolio for you.
          </p>
          <div className="hero-cta">
            <Link className="btn btn-primary btn-lg" to={terminalPath("AAPL")}>
              Open the trading terminal →
            </Link>
            <Link className="btn btn-lg" to="/backtest">
              Try a backtest
            </Link>
          </div>
          <div className="hero-note">
            Everything runs on demo money. Nothing here is financial advice.
          </div>
        </div>
      </header>

      {/* --------------------------------------------------- Top movers */}
      <section className="home-section">
        <div className="section-head">
          <h2 className="section-title">Top movers</h2>
          <span className="section-sub">24h gainers &amp; losers</span>
        </div>
        <div className="movers-grid">
          <MoversCard
            title="Top gainers"
            tone="up"
            movers={moversUp}
            failed={summaryFailed}
            onSelect={goSymbol}
          />
          <MoversCard
            title="Top losers"
            tone="down"
            movers={moversDown}
            failed={summaryFailed}
            onSelect={goSymbol}
          />
        </div>
      </section>

      <footer className="home-footer">
        <span>© {new Date().getFullYear()} QuantDeck · Paper trading, demo money only.</span>
        <div className="foot-links">
          <Link to="/terminal">Terminal</Link>
          <Link to="/strategies">Strategies</Link>
          <Link to="/backtest">Backtest</Link>
        </div>
      </footer>
    </div>
  );
}

/* ============================================================ Top nav bar */
function HomeNav() {
  return (
    <nav className="home-nav">
      <Link className="home-logo" to="/">
        <span className="logo-mark">Q</span>
        <span>
          Quant<span className="logo-accent">Deck</span>
        </span>
      </Link>
      <div className="home-nav-links">
        <Link to="/terminal">Markets</Link>
        <Link to={terminalPath("AAPL")}>Charts</Link>
        <Link to="/strategies">Strategies</Link>
        <Link to="/backtest">Backtest</Link>
      </div>
      <div className="home-nav-spacer" />
      <SymbolSearch />
      <span className="badge badge-demo" title="All trading is simulated with demo money">
        Paper · Demo money
      </span>
    </nav>
  );
}

/* ============================================================ Symbol search */
function SymbolSearch() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SymbolInfo[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const seq = useRef(0); // guards against out-of-order async responses

  // Debounced search against api.search.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      setOpen(false);
      return;
    }
    const myTurn = ++seq.current;
    const t = window.setTimeout(() => {
      api
        .search(q, 12)
        .then((rows) => {
          if (myTurn !== seq.current) return; // stale response
          setResults(rows);
          setActiveIdx(0);
          setOpen(true);
        })
        .catch(() => {
          if (myTurn !== seq.current) return;
          setResults([]);
          setOpen(true);
        });
    }, 180);
    return () => window.clearTimeout(t);
  }, [query]);

  // Close the dropdown when clicking outside.
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const choose = useCallback(
    (symbol: string) => {
      setOpen(false);
      setQuery("");
      navigate(terminalPath(symbol));
    },
    [navigate],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      const picked = results[activeIdx];
      if (picked) choose(picked.symbol);
      else if (query.trim()) choose(query.trim().toUpperCase());
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="home-search" ref={boxRef}>
      <span className="search-icon">⌕</span>
      <input
        className="input home-search-input"
        placeholder="Search symbol…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => query.trim() && setOpen(true)}
        onKeyDown={onKeyDown}
        aria-label="Search symbol"
      />
      {open && (
        <div className="home-search-results">
          {results.length === 0 ? (
            <div className="home-search-empty">No matches — press Enter to open anyway</div>
          ) : (
            results.map((r, i) => (
              <div
                key={r.symbol}
                className={`home-search-row ${i === activeIdx ? "active" : ""}`}
                onMouseEnter={() => setActiveIdx(i)}
                onMouseDown={(e) => {
                  // mousedown (not click) so it fires before input blur closes it
                  e.preventDefault();
                  choose(r.symbol);
                }}
              >
                <span className="sr-sym">{r.symbol}</span>
                <span className="sr-name">{r.name}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

/* ============================================================ Ticker tape */
function TickerTape({
  items,
  onSelect,
}: {
  items: { sym: string; q: Quote | undefined }[];
  onSelect: (symbol: string) => void;
}) {
  // Render the list twice so the CSS marquee loops seamlessly (-50% shift).
  const loop = [...items, ...items];
  return (
    <div className="ticker-tape" aria-label="Live market ticker">
      <div className="ticker-track">
        {loop.map(({ sym, q }, i) => {
          const pct = changePct(q);
          return (
            <div
              key={`${sym}-${i}`}
              className="ticker-item"
              onClick={() => onSelect(sym)}
              title={`Open ${sym} in the terminal`}
            >
              <span className="ticker-sym">{sym}</span>
              <span className="ticker-price mono">{q ? fmtPrice(q.price) : "—"}</span>
              <span className={`ticker-chg mono ${signClass(pct)}`}>
                {pct === null ? "—" : fmtPct(pct)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ============================================================ Movers card */
function MoversCard({
  title,
  tone,
  movers,
  failed,
  onSelect,
}: {
  title: string;
  tone: "up" | "down";
  movers: Quote[];
  failed: boolean;
  onSelect: (symbol: string) => void;
}) {
  return (
    <div className="card movers-card">
      <div className="panel-title mc-head">
        <span className={`mc-badge ${tone}`}>{tone === "up" ? "▲" : "▼"}</span>
        <span>{title}</span>
      </div>
      {failed ? (
        <div className="movers-placeholder">Market data unavailable right now.</div>
      ) : movers.length === 0 ? (
        <div className="movers-placeholder">Loading movers…</div>
      ) : (
        movers.slice(0, 6).map((q) => {
          const pct = changePct(q);
          return (
            <div key={q.symbol} className="mover-row" onClick={() => onSelect(q.symbol)}>
              <div className="mover-sym">
                <span className="ms-sym">{q.symbol}</span>
                <span className="ms-name">{assetLabel(q.symbol)}</span>
              </div>
              <div className="mover-price mono">{fmtPrice(q.price)}</div>
              <div className={`mover-chg mono ${signClass(pct)}`}>
                {pct === null ? "—" : fmtPct(pct)}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

/* ============================================================ Static bits */
function assetLabel(_symbol: string): string {
  return "US Equity";
}
