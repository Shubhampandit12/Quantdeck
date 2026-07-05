// TopBar — full-width terminal header: search, asset filter, timeframe, portfolio
// switcher (+ New / Reset), leverage editor, balance/equity, connection dot, PAPER badge.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import "./TopBar.css";
import { api, ApiError } from "../../api/client";
import { useStore } from "../../state/store";
import type { AssetClass, SymbolInfo } from "../../api/types";
import { fmtUsd, signClass, fmtSigned } from "../../lib/format";
import type { Timeframe } from "../../pages/TerminalPage";

const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"];

interface Props {
  timeframe: Timeframe;
  onTimeframe: (tf: Timeframe) => void;
  onSelectSymbol: (sym: string) => void;
  connected: boolean;
}

export default function TopBar({ timeframe, onTimeframe, onSelectSymbol, connected }: Props) {
  const portfolios = useStore((s) => s.portfolios);
  const activePortfolioId = useStore((s) => s.activePortfolioId);
  const setActivePortfolio = useStore((s) => s.setActivePortfolio);
  const snapshot = useStore((s) => s.snapshot);
  const loadPortfolios = useStore((s) => s.loadPortfolios);
  const refreshSnapshot = useStore((s) => s.refreshSnapshot);

  const [assetFilter, setAssetFilter] = useState<AssetClass>("crypto");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SymbolInfo[]>([]);
  const [showResults, setShowResults] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [showReset, setShowReset] = useState(false);
  const [showLev, setShowLev] = useState(false);
  const [showBalance, setShowBalance] = useState(false);

  const pf = snapshot?.portfolio;

  // Debounced symbol search filtered by the active asset class.
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(async () => {
      try {
        const r = await api.search(query.trim(), 20);
        setResults(r.filter((s) => s.asset_class === assetFilter));
      } catch {
        setResults([]);
      }
    }, 200);
  }, [query, assetFilter]);

  function pick(sym: string) {
    onSelectSymbol(sym);
    setQuery("");
    setResults([]);
    setShowResults(false);
  }

  return (
    <header className="topbar">
      <Link className="topbar-logo" to="/" title="Back to home">
        Quant<span>Deck</span>
      </Link>

      <div className="topbar-sep" />

      {/* symbol search */}
      <div className="topbar-search">
        <input
          className="input"
          placeholder="Search symbol…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setShowResults(true);
          }}
          onFocus={() => setShowResults(true)}
          onBlur={() => setTimeout(() => setShowResults(false), 150)}
        />
        {showResults && results.length > 0 && (
          <div className="topbar-results">
            {results.map((r) => (
              <div key={r.symbol} className="topbar-result" onMouseDown={() => pick(r.symbol)}>
                <span className="sym">{r.symbol}</span>
                <span className="nm">{r.name}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* asset-class filter */}
      <div className="pill-group">
        <button
          className={assetFilter === "us_equity" ? "active" : ""}
          onClick={() => setAssetFilter("us_equity")}
        >
          US Stocks
        </button>
        <button
          className={assetFilter === "crypto" ? "active" : ""}
          onClick={() => setAssetFilter("crypto")}
        >
          Crypto
        </button>
      </div>

      {/* timeframe selector */}
      <div className="pill-group">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            className={tf === timeframe ? "active" : ""}
            onClick={() => onTimeframe(tf)}
          >
            {tf}
          </button>
        ))}
      </div>

      <div className="topbar-spacer" />

      {/* portfolio switcher */}
      <div className="topbar-pf">
        <select
          className="input"
          value={activePortfolioId ?? ""}
          onChange={(e) => {
            if (e.target.value) void setActivePortfolio(e.target.value);
          }}
        >
          {portfolios.length === 0 && <option value="">No portfolios</option>}
          {portfolios.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <button className="btn" onClick={() => setShowNew(true)}>
        New
      </button>
      <button className="btn" onClick={() => setShowReset(true)} disabled={!activePortfolioId}>
        Reset
      </button>

      <div className="topbar-sep" />

      {/* leverage */}
      <div className="topbar-stat topbar-lev" onClick={() => pf && setShowLev(true)}>
        <span className="lbl">Leverage</span>
        <span className="val">{pf ? `${pf.leverage}x` : "—"}</span>
      </div>

      {/* balance / equity — click Cash to change your demo balance */}
      <div
        className="topbar-stat topbar-lev"
        onClick={() => pf && setShowBalance(true)}
        title="Click to change your demo balance"
      >
        <span className="lbl">Cash ✎</span>
        <span className="val mono">{fmtUsd(pf?.cash)}</span>
      </div>
      <div className="topbar-stat">
        <span className="lbl">Equity</span>
        <span className="val mono">{fmtUsd(pf?.equity)}</span>
      </div>
      <div className="topbar-stat">
        <span className="lbl">uPnL</span>
        <span className={`val mono ${signClass(pf?.unrealized_pnl)}`}>
          {pf ? fmtSigned(pf.unrealized_pnl) : "—"}
        </span>
      </div>

      <div className="topbar-sep" />

      {/* connection dot + PAPER badge */}
      <div className={`conn-dot ${connected ? "on" : ""}`} title={connected ? "Live" : "Disconnected"} />
      <span className="badge badge-demo">Paper · Demo</span>

      {showNew && (
        <NewPortfolioModal
          onClose={() => setShowNew(false)}
          onCreated={async (id) => {
            await loadPortfolios();
            await setActivePortfolio(id);
            setShowNew(false);
          }}
        />
      )}
      {showReset && pf && (
        <ResetPortfolioModal
          id={pf.id}
          currentBalance={pf.starting_balance}
          onClose={() => setShowReset(false)}
          onDone={async () => {
            await refreshSnapshot();
            setShowReset(false);
          }}
        />
      )}
      {showLev && pf && (
        <LeverageModal
          id={pf.id}
          current={pf.leverage}
          onClose={() => setShowLev(false)}
          onDone={async () => {
            await refreshSnapshot();
            setShowLev(false);
          }}
        />
      )}
      {showBalance && pf && (
        <BalanceModal
          id={pf.id}
          current={pf.starting_balance}
          onClose={() => setShowBalance(false)}
          onDone={async () => {
            await refreshSnapshot();
            setShowBalance(false);
          }}
        />
      )}
    </header>
  );
}

// --- Modals -------------------------------------------------------------

function NewPortfolioModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [name, setName] = useState("My Paper Portfolio");
  const [balance, setBalance] = useState(100000);
  const [leverage, setLeverage] = useState(1);
  const [fillMode, setFillMode] = useState("realistic");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const p = await api.createPortfolio({
        name: name.trim() || "Paper Portfolio",
        starting_balance: balance,
        leverage,
        fill_mode: fillMode,
      });
      onCreated(p.id);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed to create portfolio");
      setBusy(false);
    }
  }

  return (
    <ModalShell title="New paper portfolio" onClose={onClose}>
      {err && <div className="modal-err">{err}</div>}
      <div className="modal-row">
        <label>Name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="modal-row">
        <label>Starting balance (USD)</label>
        <input
          className="input"
          type="number"
          value={balance}
          onChange={(e) => setBalance(Number(e.target.value))}
        />
      </div>
      <div className="modal-row">
        <label>Leverage</label>
        <input
          className="input"
          type="number"
          min={1}
          max={100}
          value={leverage}
          onChange={(e) => setLeverage(Number(e.target.value))}
        />
      </div>
      <div className="modal-row">
        <label>Fill mode</label>
        <select className="input" value={fillMode} onChange={(e) => setFillMode(e.target.value)}>
          <option value="realistic">Realistic</option>
          <option value="idealized">Idealized</option>
        </select>
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? "Creating…" : "Create"}
        </button>
      </div>
    </ModalShell>
  );
}

function ResetPortfolioModal({
  id,
  currentBalance,
  onClose,
  onDone,
}: {
  id: string;
  currentBalance: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const [balance, setBalance] = useState(currentBalance);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      await api.resetPortfolio(id, balance);
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Reset failed");
      setBusy(false);
    }
  }

  return (
    <ModalShell title="Reset portfolio" onClose={onClose}>
      {err && <div className="modal-err">{err}</div>}
      <p className="muted" style={{ marginTop: 0 }}>
        Closes all positions/orders and resets cash. Paper money only.
      </p>
      <div className="modal-row">
        <label>New starting balance (USD)</label>
        <input
          className="input"
          type="number"
          value={balance}
          onChange={(e) => setBalance(Number(e.target.value))}
        />
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? "Resetting…" : "Reset"}
        </button>
      </div>
    </ModalShell>
  );
}

function LeverageModal({
  id,
  current,
  onClose,
  onDone,
}: {
  id: string;
  current: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const [lev, setLev] = useState(current);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      await api.setLeverage(id, lev);
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed to set leverage");
      setBusy(false);
    }
  }

  return (
    <ModalShell title="Set leverage" onClose={onClose}>
      {err && <div className="modal-err">{err}</div>}
      <div className="modal-row">
        <label>Leverage (x)</label>
        <input
          className="input"
          type="number"
          min={1}
          max={100}
          value={lev}
          onChange={(e) => setLev(Number(e.target.value))}
        />
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </ModalShell>
  );
}

// Change the demo (paper) balance with one-click presets or a custom amount.
// This resets the account to the chosen starting cash (paper money only).
function BalanceModal({
  id,
  current,
  onClose,
  onDone,
}: {
  id: string;
  current: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const [balance, setBalance] = useState(current);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const PRESETS = [10_000, 100_000, 500_000, 1_000_000, 10_000_000];

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      await api.resetPortfolio(id, balance);
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed to set balance");
      setBusy(false);
    }
  }

  return (
    <ModalShell title="Set demo balance" onClose={onClose}>
      {err && <div className="modal-err">{err}</div>}
      <p className="muted" style={{ marginTop: 0 }}>
        Set how much paper money you start with. This resets the account to the
        chosen cash and clears open positions/orders. Demo money only.
      </p>
      <div className="modal-row">
        <label>Quick amounts</label>
        <div className="modal-presets">
          {PRESETS.map((v) => (
            <button
              key={v}
              type="button"
              className={`btn modal-preset${balance === v ? " btn-primary" : ""}`}
              onClick={() => setBalance(v)}
            >
              {v >= 1_000_000 ? `$${v / 1_000_000}M` : `$${v / 1_000}k`}
            </button>
          ))}
        </div>
      </div>
      <div className="modal-row">
        <label>Custom amount (USD)</label>
        <input
          className="input"
          type="number"
          min={1}
          value={balance}
          onChange={(e) => setBalance(Number(e.target.value))}
        />
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={busy || balance <= 0}>
          {busy ? "Applying…" : `Set ${fmtUsd(balance)}`}
        </button>
      </div>
    </ModalShell>
  );
}

function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {children}
      </div>
    </div>
  );
}
