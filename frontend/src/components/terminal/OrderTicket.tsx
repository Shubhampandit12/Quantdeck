// OrderTicket — right-panel order entry. Buy/Long · Sell/Short + quantity
// only (always a market order — no limit/stop types, no leverage, no
// TP/SL: this manual form stays minimal on purpose, the AI/MCP/strategy
// path keeps full order-type/leverage/TP-SL capability unchanged).
// Submits via api.placeOrder and surfaces reject_reason inline. Below the
// form: the real open position for this symbol, if any — qty, avg entry,
// last, market value, uPnL, liq. price.
import { useState } from "react";
import "./OrderTicket.css";
import { api, ApiError } from "../../api/client";
import { useStore } from "../../state/store";
import type { OrderRequest, OrderSide } from "../../api/types";
import { fmtUsd, fmtPrice, fmtNum, fmtSigned, signClass } from "../../lib/format";
import type { Timeframe } from "../../pages/TerminalPage";

interface Props {
  symbol: string;
  timeframe: Timeframe;
}

type Msg = { kind: "err" | "ok"; text: string } | null;

export default function OrderTicket({ symbol }: Props) {
  const snapshot = useStore((s) => s.snapshot);
  const activePortfolioId = useStore((s) => s.activePortfolioId);
  const refreshSnapshot = useStore((s) => s.refreshSnapshot);

  const [side, setSide] = useState<OrderSide>("buy");
  const [qty, setQty] = useState("1");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  const pf = snapshot?.portfolio;
  const position = snapshot?.positions?.find((p) => p.symbol === symbol) ?? null;

  async function submit() {
    if (!activePortfolioId) {
      setMsg({ kind: "err", text: "No active portfolio." });
      return;
    }
    const quantity = Number(qty);
    if (!quantity || quantity <= 0) {
      setMsg({ kind: "err", text: "Enter a valid quantity." });
      return;
    }
    const order: OrderRequest = {
      symbol,
      side,
      type: "market",
      quantity,
      source: "manual",
    };
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.placeOrder(activePortfolioId, order);
      if (res.status === "rejected") {
        setMsg({ kind: "err", text: `Rejected: ${res.reject_reason ?? "unknown reason"}` });
      } else {
        setMsg({ kind: "ok", text: `Order ${res.status} · ${res.side} ${res.quantity} ${res.symbol}` });
      }
      await refreshSnapshot();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof ApiError ? e.message : "Order failed" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ticket">
      <div className="panel-title" style={{ padding: "0 0 8px", borderBottom: "none" }}>
        Order · {symbol}
      </div>

      {/* buy / sell */}
      <div className="ticket-side">
        <button className={`buy ${side === "buy" ? "active" : ""}`} onClick={() => setSide("buy")}>
          Buy / Long
        </button>
        <button className={`sell ${side === "sell" ? "active" : ""}`} onClick={() => setSide("sell")}>
          Sell / Short
        </button>
      </div>

      {/* quantity */}
      <div className="ticket-field">
        <label>Quantity</label>
        <input className="input" type="number" min={0} value={qty} onChange={(e) => setQty(e.target.value)} />
      </div>

      {/* account info */}
      <div className="ticket-info">
        <div className="ticket-info-row">
          <span>Buying power</span>
          <span className="v num">{fmtUsd(pf?.buying_power)}</span>
        </div>
        <div className="ticket-info-row">
          <span>Margin used</span>
          <span className="v num">{fmtUsd(pf?.margin_used)}</span>
        </div>
      </div>

      <button
        className={`btn ticket-submit ${side === "buy" ? "btn-buy" : "btn-sell"}`}
        onClick={submit}
        disabled={busy || !activePortfolioId}
      >
        {busy ? "Submitting…" : side === "buy" ? "Buy / Long" : "Sell / Short"}
      </button>

      {msg && <div className={`ticket-msg ${msg.kind}`}>{msg.text}</div>}

      {/* your real open position in this symbol, if any */}
      {position && (
        <div className="ticket-position">
          <div className="ticket-position-head">
            <span>Your position</span>
            <span className={`ticket-position-side side-${position.side}`}>
              {position.side.toUpperCase()}
            </span>
          </div>
          <div className="ticket-info-row">
            <span>Quantity</span>
            <span className="v num">{fmtNum(position.quantity, 4)}</span>
          </div>
          <div className="ticket-info-row">
            <span>Avg entry</span>
            <span className="v num">{fmtPrice(position.avg_entry_price)}</span>
          </div>
          <div className="ticket-info-row">
            <span>Last</span>
            <span className="v num">{fmtPrice(position.last_price)}</span>
          </div>
          <div className="ticket-info-row">
            <span>Market value</span>
            <span className="v num">{fmtUsd(position.market_value)}</span>
          </div>
          <div className="ticket-info-row">
            <span>Profit / loss</span>
            <span className={`v num ${signClass(position.unrealized_pnl)}`}>
              {fmtSigned(position.unrealized_pnl)}
            </span>
          </div>
          <div className="ticket-info-row">
            <span>Liq. price</span>
            <span className="v num">
              {position.liquidation_price != null ? fmtPrice(position.liquidation_price) : "—"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
