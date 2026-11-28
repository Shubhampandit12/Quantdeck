// BottomPanel — tabbed Positions | Open Orders | AI Session. (Trade History
// and Strategy Logs are intentionally not shown here — this UI stays
// minimal; nothing about MCP/AI capability depends on them.)
import { useCallback, useState } from "react";
import "./BottomPanel.css";
import { api } from "../../api/client";
import { useStore } from "../../state/store";
import type { Order, Position } from "../../api/types";
import { fmtPrice, fmtUsd, fmtSigned, signClass, fmtNum } from "../../lib/format";
import AISessionPanel from "../AISessionPanel";

type Tab = "positions" | "orders" | "ai";

export default function BottomPanel() {
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
        <TabBtn id="ai" tab={tab} setTab={setTab} label="AI Session" />
        <div className="bp-tabs-spacer" />
      </div>

      <div className="bp-body">
        {tab === "positions" && <PositionsTable positions={positions} />}
        {tab === "orders" && <OrdersTable orders={openOrders} />}
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
            <td className="num">{fmtNum(p.quantity, 4)}</td>
            <td className="num">{fmtPrice(p.avg_entry_price)}</td>
            <td className="num">{fmtPrice(p.last_price)}</td>
            <td className="num">{fmtUsd(p.market_value)}</td>
            <td className={`num ${signClass(p.unrealized_pnl)}`}>{fmtSigned(p.unrealized_pnl)}</td>
            <td className="num">{p.liquidation_price != null ? fmtPrice(p.liquidation_price) : "—"}</td>
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
            <td className="num">{fmtNum(o.quantity, 4)}</td>
            <td className="num">{fmtNum(o.filled_quantity, 4)}</td>
            <td className="num">{o.limit_price != null ? fmtPrice(o.limit_price) : "—"}</td>
            <td className="num">{o.stop_price != null ? fmtPrice(o.stop_price) : "—"}</td>
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

