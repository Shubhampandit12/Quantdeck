// StrategyManager — author, save, run, and monitor trading strategies.
// Left rail lists strategies; center is a monospace code editor with a
// line-number gutter; right controls start/stop against a portfolio and
// stream logs.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { openEventSocket } from "../api/ws";
import type { MarketEvent, StrategySummary } from "../api/types";
import "./StrategyManager.css";

// Default strategy shown when authoring a brand-new strategy ("Strategy A").
// It must define a Strategy subclass — that's what the sandbox loader looks for.
const EXAMPLE_CODE = `# Strategy A — three-candle momentum
#
# Idea: three candles in a row of the same colour suggests a short-term trend,
# so we ride it, and we get out as soon as one candle goes the other way.
#
#   * 3 green candles in a row  -> BUY  (go long)
#       exit: SELL (close the long) on the first red candle that follows
#   * 3 red candles in a row    -> SELL (go short)
#       exit: BUY back (close the short) on the first green candle that follows
#
# A candle is "green" when it closes above where it opened, and "red" when it
# closes below. A flat candle (close == open) is neither, so it breaks a streak.
#
# Notes:
#   * Candles are built live from streamed prices at the interval you pick next
#     to the Start button (e.g. 1m). The strategy needs 3 finished candles
#     before it can make its first decision.
#   * Every order goes through the same risk checks as a manual order.

from quantdeck.strategies import Strategy

# Fraction of current buying power to put into each new position.
POSITION_FRACTION = 0.5


def is_green(bar):
    return bar.close > bar.open


def is_red(bar):
    return bar.close < bar.open


class StrategyA(Strategy):
    name = "Strategy A"

    def on_start(self, ctx):
        ctx.log("Strategy A started: 3 green -> buy, 3 red -> sell short")

    def on_bar(self, ctx, bar):
        symbol = bar.symbol
        pos = ctx.get_position(symbol)
        side = pos.side.value if pos is not None else "flat"

        # 1) If we hold a position, only look for the exit signal.
        if side == "long":
            if is_red(bar):
                ctx.log(f"{symbol}: red candle -> closing long")
                ctx.close(symbol)
            return
        if side == "short":
            if is_green(bar):
                ctx.log(f"{symbol}: green candle -> closing short")
                ctx.close(symbol)
            return

        # 2) No position: wait until we have 3 finished candles.
        last3 = ctx.bars(symbol, 3)
        if len(last3) < 3:
            return

        qty = ctx.max_shares(symbol, POSITION_FRACTION)
        if qty <= 0:
            return

        # 3) Entry signals.
        if all(is_green(b) for b in last3):
            ctx.log(f"{symbol}: 3 green candles -> BUY {qty:.4f}")
            ctx.buy(symbol, qty)
        elif all(is_red(b) for b in last3):
            ctx.log(f"{symbol}: 3 red candles -> SELL SHORT {qty:.4f}")
            ctx.sell(symbol, qty)

    def on_stop(self, ctx):
        ctx.log("Strategy A stopped")
`;

const LOG_POLL_MS = 2000;

// A blank draft that has not been persisted yet.
type Draft = {
  id: string | null; // null => unsaved new strategy
  name: string;
  code: string;
};

const NEW_DRAFT: Draft = { id: null, name: "Strategy A", code: EXAMPLE_CODE };

function isRunning(status: string | undefined): boolean {
  return status === "running" || status === "started" || status === "live";
}

export default function StrategyManager({ portfolioId }: { portfolioId: string }) {
  const [list, setList] = useState<StrategySummary[]>([]);
  const [draft, setDraft] = useState<Draft>(NEW_DRAFT);
  const [status, setStatus] = useState<string>("idle");
  const [logs, setLogs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Run controls
  const [symbolsInput, setSymbolsInput] = useState("AAPL");
  const [interval, setIntervalStr] = useState("1h");

  const logRef = useRef<HTMLDivElement>(null);
  const running = isRunning(status);

  // --- Strategy list --------------------------------------------------------
  const loadList = useCallback(async () => {
    try {
      const items = await api.strategies();
      setList(items);
      return items;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return [];
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  // --- Load a strategy into the editor -------------------------------------
  const selectStrategy = useCallback(async (id: string) => {
    setError(null);
    setBusy(true);
    try {
      const s = await api.strategy(id);
      setDraft({ id: s.id, name: s.name, code: s.code });
      setStatus(s.status ?? "idle");
      const l = await api.strategyLogs(id);
      setLogs(l.logs);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const newStrategy = useCallback(() => {
    setError(null);
    setDraft({ ...NEW_DRAFT });
    setStatus("idle");
    setLogs([]);
  }, []);

  // --- Save (create or update) ---------------------------------------------
  const save = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      let saved: StrategySummary;
      if (draft.id === null) {
        saved = await api.createStrategy({
          name: draft.name.trim() || "Untitled",
          code: draft.code,
          portfolio_id: portfolioId,
        });
      } else {
        saved = await api.updateStrategy(draft.id, {
          name: draft.name.trim() || "Untitled",
          code: draft.code,
        });
      }
      setDraft((d) => ({ ...d, id: saved.id, name: saved.name }));
      setStatus(saved.status ?? status);
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [draft, portfolioId, status, loadList]);

  // --- Delete ---------------------------------------------------------------
  const del = useCallback(async () => {
    if (draft.id === null) {
      newStrategy();
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await api.deleteStrategy(draft.id);
      newStrategy();
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [draft.id, newStrategy, loadList]);

  // --- Start / Stop ---------------------------------------------------------
  const start = useCallback(async () => {
    if (draft.id === null) {
      setError("Save the strategy before starting it.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const symbols = symbolsInput
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const s = await api.startStrategy(draft.id, {
        portfolio_id: portfolioId,
        symbols: symbols.length ? symbols : undefined,
        interval: interval || undefined,
      });
      setStatus(s.status ?? "running");
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [draft.id, symbolsInput, interval, portfolioId, loadList]);

  const stop = useCallback(async () => {
    if (draft.id === null) return;
    setError(null);
    setBusy(true);
    try {
      const s = await api.stopStrategy(draft.id);
      setStatus(s.status ?? "idle");
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [draft.id, loadList]);

  // --- Live logs: poll while running ---------------------------------------
  useEffect(() => {
    if (draft.id === null || !running) return;
    const id = draft.id;
    let cancelled = false;
    const tick = async () => {
      try {
        const l = await api.strategyLogs(id);
        if (!cancelled) setLogs(l.logs);
      } catch {
        /* transient — keep polling */
      }
    };
    void tick();
    const handle = window.setInterval(tick, LOG_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [draft.id, running]);

  // --- Live logs/status: event socket for this portfolio -------------------
  useEffect(() => {
    if (!portfolioId) return;
    const sock = openEventSocket(portfolioId, (e: MarketEvent) => {
      if (e.type === "strategy_log") {
        const line = typeof e.data.line === "string" ? e.data.line : String(e.data.message ?? "");
        if (line) setLogs((prev) => [...prev.slice(-499), line]);
      } else if (e.type === "strategy_status") {
        const sid = typeof e.data.strategy_id === "string" ? e.data.strategy_id : null;
        const st = typeof e.data.status === "string" ? e.data.status : null;
        // Only reflect status for the strategy currently in the editor.
        setDraft((d) => {
          if (st && sid && d.id === sid) setStatus(st);
          return d;
        });
      }
    });
    return () => sock.close();
  }, [portfolioId]);

  // Auto-scroll the log pane to the newest line.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  // Line numbers gutter derived from current code.
  const lineCount = useMemo(() => draft.code.split("\n").length, [draft.code]);
  const gutter = useMemo(
    () => Array.from({ length: lineCount }, (_, i) => i + 1).join("\n"),
    [lineCount],
  );

  // Keep the gutter scroll in sync with the textarea.
  const onEditorScroll = useCallback((e: React.UIEvent<HTMLTextAreaElement>) => {
    const g = document.getElementById("qd-strategy-gutter");
    if (g) g.scrollTop = e.currentTarget.scrollTop;
  }, []);

  const statusClass = running ? "running" : status === "error" ? "error" : "idle";

  return (
    <div className="strat-mgr">
      {/* Left rail: strategy list */}
      <aside className="strat-list card">
        <div className="strat-list-head">
          <span className="panel-title" style={{ border: "none", padding: 0 }}>
            Strategies
          </span>
          <button className="btn btn-primary strat-new" onClick={newStrategy} disabled={busy}>
            + New
          </button>
        </div>
        <div className="strat-list-items">
          {list.length === 0 && <div className="strat-empty muted">No strategies yet.</div>}
          {list.map((s) => (
            <button
              key={s.id}
              className={`strat-item${draft.id === s.id ? " active" : ""}`}
              onClick={() => void selectStrategy(s.id)}
            >
              <span className="strat-item-name">{s.name}</span>
              <span className={`strat-dot ${isRunning(s.status) ? "running" : "idle"}`} />
            </button>
          ))}
        </div>
      </aside>

      {/* Center + right: editor and controls */}
      <section className="strat-main card">
        <div className="strat-toolbar">
          <input
            className="input strat-name"
            value={draft.name}
            placeholder="Strategy name"
            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
          />
          <span className={`badge strat-status ${statusClass}`}>{status}</span>
          <div className="strat-actions">
            <button className="btn btn-primary" onClick={() => void save()} disabled={busy}>
              Save
            </button>
            {running ? (
              <button className="btn btn-sell" onClick={() => void stop()} disabled={busy}>
                Stop
              </button>
            ) : (
              <button
                className="btn btn-buy"
                onClick={() => void start()}
                disabled={busy || draft.id === null}
                title={draft.id === null ? "Save first" : "Start live"}
              >
                Start
              </button>
            )}
            <button className="btn" onClick={() => void del()} disabled={busy}>
              Delete
            </button>
          </div>
        </div>

        {/* Run parameters */}
        <div className="strat-run-row">
          <label className="strat-field">
            <span className="strat-field-label">Symbols</span>
            <input
              className="input"
              value={symbolsInput}
              placeholder="AAPL, MSFT"
              onChange={(e) => setSymbolsInput(e.target.value)}
              disabled={running}
            />
          </label>
          <label className="strat-field strat-field-sm">
            <span className="strat-field-label">Interval</span>
            <select
              className="input"
              value={interval}
              onChange={(e) => setIntervalStr(e.target.value)}
              disabled={running}
            >
              {["1m", "5m", "15m", "1h", "4h", "1d", "1w"].map((iv) => (
                <option key={iv} value={iv}>
                  {iv}
                </option>
              ))}
            </select>
          </label>
        </div>

        {error && <div className="strat-error">{error}</div>}

        {/* Code editor: line-number gutter + monospace textarea */}
        <div className="strat-editor">
          <pre id="qd-strategy-gutter" className="strat-gutter mono" aria-hidden="true">
            {gutter}
          </pre>
          <textarea
            className="strat-code mono"
            spellCheck={false}
            value={draft.code}
            onChange={(e) => setDraft((d) => ({ ...d, code: e.target.value }))}
            onScroll={onEditorScroll}
            wrap="off"
          />
        </div>

        {/* Logs pane */}
        <div className="strat-logs">
          <div className="panel-title">Logs {running && <span className="strat-live">live</span>}</div>
          <div className="strat-logs-body mono" ref={logRef}>
            {logs.length === 0 ? (
              <div className="muted strat-logs-empty">No logs yet.</div>
            ) : (
              logs.map((line, i) => (
                <div key={i} className="strat-log-line">
                  {line}
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
