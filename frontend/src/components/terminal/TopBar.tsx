// TopBar — full-width terminal header: timeframe, Reset, balance/equity,
// connection dot, PAPER badge. No portfolio switcher or "New" (the app
// auto-creates one "Default" portfolio; this is a single-portfolio demo),
// no manual leverage editor (fixed at 1x — the AI/MCP/strategy path can
// still set leverage via api.setLeverage, unchanged), no top-bar symbol
// search (use the Watchlist's own filter).
import { useState } from "react";
import { Link } from "react-router-dom";
import "./TopBar.css";
import { api, ApiError } from "../../api/client";
import { useStore } from "../../state/store";
import { fmtUsd, signClass, fmtSigned } from "../../lib/format";
import type { Timeframe } from "../../pages/TerminalPage";

const TIMEFRAMES: Timeframe[] = ["1m", "5m", "1h", "4h"];

interface Props {
  timeframe: Timeframe;
  onTimeframe: (tf: Timeframe) => void;
  connected: boolean;
}

export default function TopBar({ timeframe, onTimeframe, connected }: Props) {
  const snapshot = useStore((s) => s.snapshot);
  const refreshSnapshot = useStore((s) => s.refreshSnapshot);

  const [showReset, setShowReset] = useState(false);
  const [showBalance, setShowBalance] = useState(false);

  const pf = snapshot?.portfolio;

  return (
    <header className="topbar">
      <Link className="topbar-logo" to="/" title="Back to home">
        Quant<span>Deck</span>
      </Link>

      <div className="topbar-sep" />

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

      <button className="btn" onClick={() => setShowReset(true)} disabled={!pf}>
        Reset
      </button>

      <div className="topbar-sep" />

      {/* balance / equity — click Cash to change your demo balance */}
      <div
        className="topbar-stat topbar-lev"
        onClick={() => pf && setShowBalance(true)}
        title="Click to change your demo balance"
      >
        <span className="lbl">Cash ✎</span>
        <span className="val num">{fmtUsd(pf?.cash)}</span>
      </div>
      <div className="topbar-stat">
        <span className="lbl">Equity</span>
        <span className="val num">{fmtUsd(pf?.equity)}</span>
      </div>
      <div className="topbar-stat">
        <span className="lbl">uPnL</span>
        <span className={`val num ${signClass(pf?.unrealized_pnl)}`}>
          {pf ? fmtSigned(pf.unrealized_pnl) : "—"}
        </span>
      </div>

      <div className="topbar-sep" />

      {/* connection dot */}
      <div className={`conn-dot ${connected ? "on" : ""}`} title={connected ? "Live" : "Disconnected"} />

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
