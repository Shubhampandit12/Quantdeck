// BottomPanel — tabbed Positions | Open Orders | Trade History | Strategy Logs | AI Session.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./BottomPanel.css";
import { api, ApiError } from "../../api/client";
import { useStore } from "../../state/store";
import type { Fill, Order, Position } from "../../api/types";
import { fmtPrice, fmtUsd, fmtSigned, signClass, fmtNum } from "../../lib/format";
import AISessionPanel from "../AISessionPanel";
import type { LogLine } from "../../pages/TerminalPage";

type Tab = "positions" | "orders" | "history" | "logs" | "ai";

interface Props {
  strategyLogs: LogLine[];
  onClearLogs: () => void;
}

export default function BottomPanel({ strategyLogs, onClearLogs }: Props) {
  const [tab, setTab] = useState<Tab>("positions");
  const snapshot = useStore((s) => s.snapshot);
  const activePortfolioId = useStore((s) => s.activePortfolioId);

  const positions = snapshot?.positions ?? [];
  const openOrders = snapshot?.open_orders ?? [];

  return (
    <div className="bottompanel">
      <div className="bp-tabs">
        <TabBtn id="positions" tab={tab} setTab={setTab} label="Positions" count={positions.length} />
        <TabBtn id="orders" tab={tab} setTab={setTab} label="Open Orders" count={openOrders.length} />
        <TabBtn id="history" tab={tab} setTab={setTab} label="Trade History" />
        <TabBtn id="logs" tab={tab} setTab={setTab} label="Strategy Logs" count={strategyLogs.length} />
        <TabBtn id="ai" tab={tab} setTab={setTab} label="AI Session" />
        <div className="bp-tabs-spacer" />
        {tab === "logs" && (
          <button className="btn" onClick={onClearLogs}>
            Clear
          </button>
        )}
      </div>

      <div className="bp-body">
        {tab === "positions" && <PositionsTable positions={positions} />}
        {tab === "orders" && <OrdersTable orders={openOrders} />}
        {tab === "history" && <HistoryTable portfolioId={activePortfolioId} refreshKey={snapshot} />}
        {tab === "logs" && <LogsView logs={strategyLogs} portfolioId={activePortfolioId} />}
        {tab === "ai" &&
          (activePortfolioId ? (
            <AISessionPanel portfolioId={activePortfolioId} />
          ) : (
            <div className="bp-empty">Select a portfolio to use AI sessions.</div>
          ))}
      </div>
    </div>
  );
}

function TabBtn({
  id,
  tab,
  setTab,
  label,
  count,
}: {
  id: Tab;
  tab: Tab;
  setTab: (t: Tab) => void;
  label: string;
  count?: number;
}) {
  return (
    <button className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
      {label}
      {count !== undefined && count > 0 && <span className="bp-count">{count}</span>}
    </button>
  );
}

// --- Positions ----------------------------------------------------------

function PositionsTable({ positions }: { positions: Position[] }) {
  const activePortfolioId = useStore((s) => s.activePortfolioId);
  const refreshSnapshot = useStore((s) => s.refreshSnapshot);
  const [closing, setClosing] = useState<string | null>(null);

  // Market-close a position: submit an opposite-side market order for its qty.
  const closePosition = useCallback(
    async (p: Position) => {
      if (!activePortfolioId) return;
      setClosing(p.symbol);
      try {
        await api.placeOrder(activePortfolioId, {
          symbol: p.symbol,
          side: p.side === "long" ? "sell" : "buy",
          type: "market",
          quantity: Math.abs(p.quantity),
          source: "manual",
        });
        await refreshSnapshot();
      } catch {
        /* surfaced elsewhere; keep table responsive */
      } finally {
        setClosing(null);
      }
    },
    [activePortfolioId, refreshSnapshot],
  );

  if (positions.length === 0) return <div className="bp-empty">No open positions.</div>;

  return (
    <table className="bp-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Avg entry</th>
          <th>Last</th>
          <th>Market value</th>
          <th>uPnL</th>
          <th>Liq. price</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <tr key={p.symbol}>
            <td>{p.symbol}</td>
            <td className={`side-${p.side}`}>{p.side.toUpperCase()}</td>
            <td className="mono">{fmtNum(p.quantity, 4)}</td>
            <td className="mono">{fmtPrice(p.avg_entry_price)}</td>
            <td className="mono">{fmtPrice(p.last_price)}</td>
            <td className="mono">{fmtUsd(p.market_value)}</td>
            <td className={`mono ${signClass(p.unrealized_pnl)}`}>{fmtSigned(p.unrealized_pnl)}</td>
            <td className="mono">{p.liquidation_price != null ? fmtPrice(p.liquidation_price) : "—"}</td>
            <td>
              <button
                className="btn bp-row-btn"
                onClick={() => closePosition(p)}
                disabled={closing === p.symbol}
              >
                {closing === p.symbol ? "…" : "Close"}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- Open orders --------------------------------------------------------

function OrdersTable({ orders }: { orders: Order[] }) {
  const activePortfolioId = useStore((s) => s.activePortfolioId);
  const refreshSnapshot = useStore((s) => s.refreshSnapshot);
  const [cancelling, setCancelling] = useState<string | null>(null);

  const cancel = useCallback(
    async (o: Order) => {
      if (!activePortfolioId) return;
      setCancelling(o.id);
      try {
        await api.cancelOrder(activePortfolioId, o.id);
        await refreshSnapshot();
      } catch {
        /* ignore */
      } finally {
        setCancelling(null);
      }
    },
    [activePortfolioId, refreshSnapshot],
  );

  if (orders.length === 0) return <div className="bp-empty">No open orders.</div>;

  return (
    <table className="bp-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Side</th>
          <th>Type</th>
          <th>Qty</th>
          <th>Filled</th>
          <th>Limit</th>
          <th>Stop</th>
          <th>Status</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {orders.map((o) => (
          <tr key={o.id}>
            <td>{o.symbol}</td>
            <td className={`side-${o.side}`}>{o.side.toUpperCase()}</td>
            <td>{o.type}</td>
            <td className="mono">{fmtNum(o.quantity, 4)}</td>
            <td className="mono">{fmtNum(o.filled_quantity, 4)}</td>
            <td className="mono">{o.limit_price != null ? fmtPrice(o.limit_price) : "—"}</td>
            <td className="mono">{o.stop_price != null ? fmtPrice(o.stop_price) : "—"}</td>
            <td>{o.status}</td>
            <td>
              <button className="btn bp-row-btn" onClick={() => cancel(o)} disabled={cancelling === o.id}>
                {cancelling === o.id ? "…" : "Cancel"}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- Trade history (fills) ---------------------------------------------

function HistoryTable({ portfolioId, refreshKey }: { portfolioId: string | null; refreshKey: unknown }) {
  const [fills, setFills] = useState<Fill[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!portfolioId) {
      setFills([]);
      return;
    }
    let alive = true;
    setLoading(true);
    api
      .fills(portfolioId, 200)
      .then((f) => {
        if (alive) setFills(f);
      })
      .catch(() => {
        if (alive) setFills([]);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [portfolioId, refreshKey]);

  if (loading && fills.length === 0) return <div className="bp-empty">Loading…</div>;
  if (fills.length === 0) return <div className="bp-empty">No fills yet.</div>;

  return (
    <table className="bp-table">
      <thead>
        <tr>
          <th>Time</th>
          <th>Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Price</th>
          <th>Commission</th>
          <th>Slippage</th>
        </tr>
      </thead>
      <tbody>
        {fills.map((f) => (
          <tr key={f.id}>
            <td>{new Date(f.timestamp).toLocaleString()}</td>
            <td>{f.symbol}</td>
            <td className={`side-${f.side}`}>{f.side.toUpperCase()}</td>
            <td className="mono">{fmtNum(f.quantity, 4)}</td>
            <td className="mono">{fmtPrice(f.price)}</td>
            <td className="mono">{fmtUsd(f.commission)}</td>
            <td className="mono">{fmtNum(f.slippage, 4)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- Strategy logs ------------------------------------------------------

function LogsView({ logs, portfolioId }: { logs: LogLine[]; portfolioId: string | null }) {
  // Also pull any recent server-side strategy logs for strategies bound to this
  // portfolio (best-effort — the primary stream is the live events feed).
  const [serverLogs, setServerLogs] = useState<string[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!portfolioId) return;
    let alive = true;
    api
      .strategies()
      .then(async (strats) => {
        const mine = strats.filter((s) => s.portfolio_id === portfolioId);
        const chunks: string[] = [];
        for (const s of mine) {
          try {
            const r = await api.strategyLogs(s.id, 100);
            r.logs.forEach((l) => chunks.push(`[${s.name}] ${l}`));
          } catch (e) {
            if (e instanceof ApiError) chunks.push(`[${s.name}] (logs unavailable)`);
          }
        }
        if (alive) setServerLogs(chunks);
      })
      .catch(() => {
        if (alive) setServerLogs([]);
      });
    return () => {
      alive = false;
    };
  }, [portfolioId]);

  // Autoscroll to newest.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs.length, serverLogs.length]);

  const combined = useMemo(() => {
    const live = logs.map((l) => ({ ts: l.ts, text: l.text }));
    const srv = serverLogs.map((t) => ({ ts: "", text: t }));
    return [...srv, ...live];
  }, [logs, serverLogs]);

  if (combined.length === 0) return <div className="bp-empty">No strategy activity yet.</div>;

  return (
    <div className="bp-logs" ref={scrollRef}>
      {combined.map((l, i) => (
        <div className="bp-log-line" key={i}>
          {l.ts && <span className="bp-log-ts">{new Date(l.ts).toLocaleTimeString()}</span>}
          {l.text}
        </div>
      ))}
    </div>
  );
}
