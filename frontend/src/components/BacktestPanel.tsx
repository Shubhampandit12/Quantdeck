// BacktestPanel — configure + run a backtest and render its results.
// Leads with a big headline P&L, then an equity-curve chart, a metrics grid,
// and a trades table. Surfaces data_warnings and errors prominently.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from "lightweight-charts";
import { api, ApiError } from "../api/client";
import type {
  BacktestRequest,
  BacktestResult,
  StrategySummary,
  TradeRecord,
} from "../api/types";
import { fmtNum, fmtPct, fmtPrice, fmtUsd, signClass } from "../lib/format";
import "./BacktestPanel.css";

// A sensible default strategy body so the editor is never empty.
const DEFAULT_CODE = `# QuantDeck strategy — runs against historical bars.
# 'ctx' exposes market data + order helpers (see docs).
def on_bar(ctx):
    fast = ctx.sma(ctx.symbol, 10)
    slow = ctx.sma(ctx.symbol, 30)
    if fast is None or slow is None:
        return
    pos = ctx.position(ctx.symbol)
    if fast > slow and pos.quantity <= 0:
        ctx.buy(ctx.symbol, ctx.max_qty(ctx.symbol))
    elif fast < slow and pos.quantity > 0:
        ctx.sell(ctx.symbol, pos.quantity)
`;

const INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"] as const;
// Must match the backend FillMode enum (core/enums.py); the backtest engine
// only treats "idealized" specially, everything else is "realistic".
const FILL_MODES = ["idealized", "realistic"] as const;

// Which strategy source to submit: inline code vs. a saved strategy.
type SourceMode = "code" | "saved";

interface Props {
  defaultSymbol?: string;
}

export default function BacktestPanel({ defaultSymbol }: Props) {
  // ---- form state ---------------------------------------------------------
  const [symbols, setSymbols] = useState(defaultSymbol ?? "BTC/USDT");
  const [interval, setInterval] = useState<string>("1d");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [startingBalance, setStartingBalance] = useState("100000");
  const [leverage, setLeverage] = useState("1");
  const [commissionBps, setCommissionBps] = useState("5");
  const [slippageBps, setSlippageBps] = useState("2");
  const [fillMode, setFillMode] = useState<string>("realistic");

  const [sourceMode, setSourceMode] = useState<SourceMode>("code");
  const [code, setCode] = useState(DEFAULT_CODE);
  const [strategyId, setStrategyId] = useState("");
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);

  // ---- run state ----------------------------------------------------------
  const [loading, setLoading] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);

  // Load saved strategies for the "use saved strategy" picker.
  useEffect(() => {
    let active = true;
    api
      .strategies()
      .then((list) => {
        if (active) setStrategies(list);
      })
      .catch(() => {
        /* non-fatal — user can still paste code */
      });
    return () => {
      active = false;
    };
  }, []);

  const parsedSymbols = useMemo(
    () =>
      symbols
        .split(",")
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean),
    [symbols],
  );

  async function runBacktest() {
    setRunError(null);
    // Client-side validation before the (possibly long) request.
    if (parsedSymbols.length === 0) {
      setRunError("Enter at least one symbol.");
      return;
    }
    if (sourceMode === "saved" && !strategyId) {
      setRunError("Select a saved strategy or switch to inline code.");
      return;
    }
    if (sourceMode === "code" && !code.trim()) {
      setRunError("Strategy code is empty.");
      return;
    }

    const body: BacktestRequest = {
      symbols: parsedSymbols,
      interval,
      starting_balance: Number(startingBalance) || 0,
      leverage: Number(leverage) || 1,
      fill_mode: fillMode,
      commission_bps: Number(commissionBps) || 0,
      slippage_bps: Number(slippageBps) || 0,
      ...(sourceMode === "code" ? { code } : { strategy_id: strategyId }),
      ...(start ? { start } : {}),
      ...(end ? { end } : {}),
    };

    setLoading(true);
    setResult(null);
    try {
      const res = await api.backtest(body);
      setResult(res);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `Backtest failed (${e.status}): ${e.message}`
          : e instanceof Error
            ? e.message
            : "Backtest failed.";
      setRunError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bt-panel">
      <BacktestForm
        symbols={symbols}
        setSymbols={setSymbols}
        interval={interval}
        setInterval={setInterval}
        start={start}
        setStart={setStart}
        end={end}
        setEnd={setEnd}
        startingBalance={startingBalance}
        setStartingBalance={setStartingBalance}
        leverage={leverage}
        setLeverage={setLeverage}
        commissionBps={commissionBps}
        setCommissionBps={setCommissionBps}
        slippageBps={slippageBps}
        setSlippageBps={setSlippageBps}
        fillMode={fillMode}
        setFillMode={setFillMode}
        sourceMode={sourceMode}
        setSourceMode={setSourceMode}
        code={code}
        setCode={setCode}
        strategyId={strategyId}
        setStrategyId={setStrategyId}
        strategies={strategies}
        loading={loading}
        onRun={runBacktest}
      />

      {runError && (
        <div className="bt-banner bt-banner-error" role="alert">
          <strong>Error:</strong> {runError}
        </div>
      )}

      {loading && (
        <div className="bt-loading card">
          <div className="bt-spinner" aria-hidden />
          <span>Running backtest… this can take a moment for large ranges.</span>
        </div>
      )}

      {result && !loading && <BacktestResultView result={result} />}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Form                                                                        */
/* -------------------------------------------------------------------------- */

interface FormProps {
  symbols: string;
  setSymbols: (v: string) => void;
  interval: string;
  setInterval: (v: string) => void;
  start: string;
  setStart: (v: string) => void;
  end: string;
  setEnd: (v: string) => void;
  startingBalance: string;
  setStartingBalance: (v: string) => void;
  leverage: string;
  setLeverage: (v: string) => void;
  commissionBps: string;
  setCommissionBps: (v: string) => void;
  slippageBps: string;
  setSlippageBps: (v: string) => void;
  fillMode: string;
  setFillMode: (v: string) => void;
  sourceMode: SourceMode;
  setSourceMode: (v: SourceMode) => void;
  code: string;
  setCode: (v: string) => void;
  strategyId: string;
  setStrategyId: (v: string) => void;
  strategies: StrategySummary[];
  loading: boolean;
  onRun: () => void;
}

function BacktestForm(p: FormProps) {
  return (
    <div className="bt-form card">
      <div className="panel-title">Backtest configuration</div>
      <div className="bt-form-body">
        <div className="bt-grid">
          <label className="bt-field bt-field-wide">
            <span>Symbols (comma-separated)</span>
            <input
              className="input mono"
              value={p.symbols}
              onChange={(e) => p.setSymbols(e.target.value)}
              placeholder="BTC/USDT, ETH/USDT, AAPL"
            />
          </label>

          <label className="bt-field">
            <span>Interval</span>
            <select
              className="input"
              value={p.interval}
              onChange={(e) => p.setInterval(e.target.value)}
            >
              {INTERVALS.map((iv) => (
                <option key={iv} value={iv}>
                  {iv}
                </option>
              ))}
            </select>
          </label>

          <label className="bt-field">
            <span>Fill mode</span>
            <select
              className="input"
              value={p.fillMode}
              onChange={(e) => p.setFillMode(e.target.value)}
            >
              {FILL_MODES.map((fm) => (
                <option key={fm} value={fm}>
                  {fm}
                </option>
              ))}
            </select>
          </label>

          <label className="bt-field">
            <span>Start date</span>
            <input
              className="input"
              type="date"
              value={p.start}
              onChange={(e) => p.setStart(e.target.value)}
            />
          </label>

          <label className="bt-field">
            <span>End date</span>
            <input
              className="input"
              type="date"
              value={p.end}
              onChange={(e) => p.setEnd(e.target.value)}
            />
          </label>

          <label className="bt-field">
            <span>Starting balance ($)</span>
            <input
              className="input mono"
              type="number"
              min="0"
              step="1000"
              value={p.startingBalance}
              onChange={(e) => p.setStartingBalance(e.target.value)}
            />
          </label>

          <label className="bt-field">
            <span>Leverage (x)</span>
            <input
              className="input mono"
              type="number"
              min="1"
              step="1"
              value={p.leverage}
              onChange={(e) => p.setLeverage(e.target.value)}
            />
          </label>

          <label className="bt-field">
            <span>Commission (bps)</span>
            <input
              className="input mono"
              type="number"
              min="0"
              step="1"
              value={p.commissionBps}
              onChange={(e) => p.setCommissionBps(e.target.value)}
            />
          </label>

          <label className="bt-field">
            <span>Slippage (bps)</span>
            <input
              className="input mono"
              type="number"
              min="0"
              step="1"
              value={p.slippageBps}
              onChange={(e) => p.setSlippageBps(e.target.value)}
            />
          </label>
        </div>

        {/* Strategy source: inline code editor OR a saved strategy. */}
        <div className="bt-source">
          <div className="bt-source-tabs">
            <button
              type="button"
              className={`btn bt-tab ${p.sourceMode === "code" ? "bt-tab-active" : ""}`}
              onClick={() => p.setSourceMode("code")}
            >
              Code editor
            </button>
            <button
              type="button"
              className={`btn bt-tab ${p.sourceMode === "saved" ? "bt-tab-active" : ""}`}
              onClick={() => p.setSourceMode("saved")}
            >
              Saved strategy
            </button>
          </div>

          {p.sourceMode === "code" ? (
            <textarea
              className="input mono bt-code"
              spellCheck={false}
              value={p.code}
              onChange={(e) => p.setCode(e.target.value)}
              placeholder="def on_bar(ctx): ..."
            />
          ) : (
            <label className="bt-field bt-field-wide">
              <span>Strategy</span>
              <select
                className="input"
                value={p.strategyId}
                onChange={(e) => p.setStrategyId(e.target.value)}
              >
                <option value="">— select a saved strategy —</option>
                {p.strategies.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.status})
                  </option>
                ))}
              </select>
              {p.strategies.length === 0 && (
                <span className="bt-hint muted">
                  No saved strategies found — create one on the Strategies page.
                </span>
              )}
            </label>
          )}
        </div>

        <div className="bt-actions">
          <button
            type="button"
            className="btn btn-primary bt-run"
            onClick={p.onRun}
            disabled={p.loading}
          >
            {p.loading ? "Running…" : "Run backtest"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Result view                                                                 */
/* -------------------------------------------------------------------------- */

function BacktestResultView({ result }: { result: BacktestResult }) {
  const pnlSign = signClass(result.total_pnl);

  return (
    <div className="bt-result">
      {/* Engine-reported error (partial result may still exist). */}
      {result.error && (
        <div className="bt-banner bt-banner-error" role="alert">
          <strong>Strategy error:</strong> {result.error}
        </div>
      )}

      {/* Data warnings — always prominent. */}
      {result.data_warnings.length > 0 && (
        <div className="bt-banner bt-banner-warn" role="alert">
          <strong>Data warnings</strong>
          <ul>
            {result.data_warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Headline P&L. */}
      <div className="bt-headline card">
        <div className="bt-headline-main">
          <span className="bt-headline-label">Total P&amp;L</span>
          <span className={`bt-headline-pnl ${pnlSign}`}>
            {fmtUsd(result.total_pnl)}
          </span>
          <span className={`bt-headline-pct ${pnlSign}`}>
            {fmtPct(result.total_return_pct)}
          </span>
        </div>
        <div className="bt-headline-side">
          <div className="bt-headline-kv">
            <span className="muted">Starting balance</span>
            <span className="mono">{fmtUsd(result.starting_balance)}</span>
          </div>
          <div className="bt-headline-kv">
            <span className="muted">Ending balance</span>
            <span className={`mono ${pnlSign}`}>
              {fmtUsd(result.ending_balance)}
            </span>
          </div>
        </div>
      </div>

      <EquityCurveChart curve={result.equity_curve} />

      <MetricsGrid result={result} />

      <TradesTable trades={result.trades} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Equity curve chart (lightweight-charts line series)                         */
/* -------------------------------------------------------------------------- */

// Convert an ISO timestamp to UNIX seconds for lightweight-charts.
function isoToUnix(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function EquityCurveChart({ curve }: { curve: BacktestResult["equity_curve"] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  // Build de-duplicated, ascending line data (chart requires sorted, unique times).
  const lineData = useMemo<LineData[]>(() => {
    const seen = new Map<number, number>();
    for (const p of curve) {
      const t = isoToUnix(p.timestamp);
      if (!Number.isFinite(t)) continue;
      seen.set(t, p.equity); // last write wins for a duplicate timestamp
    }
    return [...seen.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([time, value]) => ({ time: time as UTCTimestamp, value }));
  }, [curve]);

  // Create the chart once.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const styles = getComputedStyle(document.documentElement);
    const cssVar = (name: string, fallback: string) =>
      styles.getPropertyValue(name).trim() || fallback;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: 260,
      layout: {
        background: { type: ColorType.Solid, color: cssVar("--bg-1", "#131722") },
        textColor: cssVar("--text-1", "#a3a6af"),
        fontFamily: cssVar("--font-sans", "sans-serif"),
      },
      grid: {
        vertLines: { color: cssVar("--border", "#2a2e39") },
        horzLines: { color: cssVar("--border", "#2a2e39") },
      },
      rightPriceScale: { borderColor: cssVar("--border-strong", "#363a45") },
      timeScale: {
        borderColor: cssVar("--border-strong", "#363a45"),
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { horzLine: { labelVisible: true }, vertLine: { labelVisible: true } },
    });

    const series = chart.addLineSeries({
      color: cssVar("--accent", "#2962ff"),
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: true,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Keep the chart sized to its container.
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) chart.applyOptions({ width: Math.floor(w) });
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Push data whenever it changes.
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    series.setData(lineData);
    chart.timeScale().fitContent();
  }, [lineData]);

  return (
    <div className="bt-chart card">
      <div className="panel-title">Equity curve</div>
      {lineData.length === 0 ? (
        <div className="bt-empty muted">No equity data returned.</div>
      ) : (
        <div ref={containerRef} className="bt-chart-canvas" />
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Metrics grid                                                                */
/* -------------------------------------------------------------------------- */

function MetricsGrid({ result }: { result: BacktestResult }) {
  const items: { label: string; value: string; cls?: string }[] = [
    {
      label: "Total return",
      value: fmtPct(result.total_return_pct),
      cls: signClass(result.total_return_pct),
    },
    {
      label: "Annualized",
      value:
        result.annualized_return_pct === null ||
        result.annualized_return_pct === undefined
          ? "—"
          : fmtPct(result.annualized_return_pct),
      cls:
        result.annualized_return_pct === null ||
        result.annualized_return_pct === undefined
          ? ""
          : signClass(result.annualized_return_pct),
    },
    {
      // Max drawdown is a loss magnitude — always shown red when non-zero.
      label: "Max drawdown",
      value: fmtPct(-Math.abs(result.max_drawdown_pct)),
      cls: result.max_drawdown_pct ? "down" : "",
    },
    {
      label: "Sharpe ratio",
      value:
        result.sharpe_ratio === null || result.sharpe_ratio === undefined
          ? "—"
          : fmtNum(result.sharpe_ratio, 2),
    },
    {
      label: "Win rate",
      value: fmtPct(result.win_rate),
    },
    {
      label: "# Trades",
      value: String(result.num_trades),
    },
    {
      label: "Profit factor",
      value:
        result.profit_factor === null || result.profit_factor === undefined
          ? "—"
          : fmtNum(result.profit_factor, 2),
    },
  ];

  return (
    <div className="bt-metrics card">
      <div className="panel-title">Performance metrics</div>
      <div className="bt-metrics-grid">
        {items.map((m) => (
          <div key={m.label} className="bt-metric">
            <span className="bt-metric-label muted">{m.label}</span>
            <span className={`bt-metric-value mono ${m.cls ?? ""}`}>{m.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Trades table                                                                */
/* -------------------------------------------------------------------------- */

function TradesTable({ trades }: { trades: TradeRecord[] }) {
  return (
    <div className="bt-trades card">
      <div className="panel-title">Trades ({trades.length})</div>
      {trades.length === 0 ? (
        <div className="bt-empty muted">No trades were executed.</div>
      ) : (
        <div className="bt-trades-scroll">
          <table className="bt-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th className="num">Qty</th>
                <th className="num">Entry</th>
                <th className="num">Exit</th>
                <th className="num">P&amp;L</th>
                <th className="num">Return</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i}>
                  <td className="mono">{t.symbol}</td>
                  <td>
                    <span className={`bt-side ${t.side === "buy" ? "up" : "down"}`}>
                      {t.side.toUpperCase()}
                    </span>
                  </td>
                  <td className="num mono">{fmtNum(t.quantity, 4)}</td>
                  <td className="num mono">{fmtPrice(t.entry_price)}</td>
                  <td className="num mono">
                    {t.exit_price === null || t.exit_price === undefined
                      ? "—"
                      : fmtPrice(t.exit_price)}
                  </td>
                  <td className={`num mono ${signClass(t.pnl)}`}>{fmtUsd(t.pnl)}</td>
                  <td className={`num mono ${signClass(t.return_pct)}`}>
                    {fmtPct(t.return_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
