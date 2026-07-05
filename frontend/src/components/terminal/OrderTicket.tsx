// OrderTicket — right-panel order entry. Side/type/qty/price/TP-SL/leverage,
// submits via api.placeOrder, shows buying power + margin + estimated liq,
// and surfaces reject_reason inline for rejected orders.
import { useMemo, useState } from "react";
import "./OrderTicket.css";
import { api, ApiError } from "../../api/client";
import { useStore } from "../../state/store";
import type { OrderRequest, OrderSide, OrderType } from "../../api/types";
import { fmtUsd, fmtPrice } from "../../lib/format";
import type { Timeframe } from "../../pages/TerminalPage";

interface Props {
  symbol: string;
  timeframe: Timeframe;
}

const TYPES: { id: OrderType; label: string }[] = [
  { id: "market", label: "Market" },
  { id: "limit", label: "Limit" },
  { id: "stop", label: "Stop" },
  { id: "stop_limit", label: "Stop Lmt" },
];

type Msg = { kind: "err" | "ok"; text: string } | null;

export default function OrderTicket({ symbol }: Props) {
  const snapshot = useStore((s) => s.snapshot);
  const activePortfolioId = useStore((s) => s.activePortfolioId);
  const refreshSnapshot = useStore((s) => s.refreshSnapshot);
  const quotes = useStore((s) => s.quotes);

  const [side, setSide] = useState<OrderSide>("buy");
  const [type, setType] = useState<OrderType>("market");
  const [qty, setQty] = useState("1");
  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [useTp, setUseTp] = useState(false);
  const [tp, setTp] = useState("");
  const [useSl, setUseSl] = useState(false);
  const [sl, setSl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [levBusy, setLevBusy] = useState(false);

  const pf = snapshot?.portfolio;
  const liveQuote = quotes[symbol];
  const refPrice = liveQuote?.price ?? null;
  const leverage = pf?.leverage ?? 1;

  // Change leverage right from the ticket (persists to the portfolio, same as
  // the TopBar control). Applies to the buy/long and sell/short you place next.
  async function changeLeverage(next: number) {
    if (!activePortfolioId || !Number.isFinite(next)) return;
    const clamped = Math.max(1, Math.min(125, next));
    setLevBusy(true);
    try {
      await api.setLeverage(activePortfolioId, clamped);
      await refreshSnapshot();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof ApiError ? e.message : "Failed to set leverage" });
    } finally {
      setLevBusy(false);
    }
  }

  const needsLimit = type === "limit" || type === "stop_limit";
  const needsStop = type === "stop" || type === "stop_limit";

  // Estimated notional and margin for the current inputs.
  const estimate = useMemo(() => {
    const q = Number(qty) || 0;
    const px = needsLimit ? Number(limitPrice) || refPrice || 0 : refPrice || 0;
    const notional = q * px;
    const margin = leverage > 0 ? notional / leverage : notional;
    // Rough liquidation estimate: entry adjusted by ~1/leverage move (maintenance ignored).
    let liq: number | null = null;
    if (px > 0 && leverage > 0) {
      const move = px / leverage;
      liq = side === "buy" ? px - move : px + move;
    }
    return { notional, margin, liq, px };
  }, [qty, limitPrice, refPrice, needsLimit, leverage, side]);

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
      type,
      quantity,
      limit_price: needsLimit ? Number(limitPrice) || null : null,
      stop_price: needsStop ? Number(stopPrice) || null : null,
      take_profit: useTp ? Number(tp) || null : null,
      stop_loss: useSl ? Number(sl) || null : null,
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

      {/* order type */}
      <div className="ticket-types">
        {TYPES.map((t) => (
          <button key={t.id} className={type === t.id ? "active" : ""} onClick={() => setType(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* quantity */}
      <div className="ticket-field">
        <label>Quantity</label>
        <input className="input" type="number" min={0} value={qty} onChange={(e) => setQty(e.target.value)} />
      </div>

      {/* price fields per type */}
      {(needsLimit || needsStop) && (
        <div className="ticket-grid2">
          {needsLimit && (
            <div className="ticket-field">
              <label>Limit price</label>
              <input
                className="input"
                type="number"
                value={limitPrice}
                placeholder={refPrice ? fmtPrice(refPrice) : ""}
                onChange={(e) => setLimitPrice(e.target.value)}
              />
            </div>
          )}
          {needsStop && (
            <div className="ticket-field">
              <label>Stop price</label>
              <input
                className="input"
                type="number"
                value={stopPrice}
                placeholder={refPrice ? fmtPrice(refPrice) : ""}
                onChange={(e) => setStopPrice(e.target.value)}
              />
            </div>
          )}
        </div>
      )}

      {/* leverage — editable right here; applies to the position you open next */}
      <div className="ticket-field">
        <label>Leverage · {leverage}x</label>
        <div className="ticket-lev">
          {[1, 2, 5, 10, 20, 50].map((l) => (
            <button
              key={l}
              type="button"
              className={`ticket-lev-btn${leverage === l ? " active" : ""}`}
              disabled={levBusy}
              onClick={() => changeLeverage(l)}
            >
              {l}x
            </button>
          ))}
          <input
            className="input ticket-lev-input"
            type="number"
            min={1}
            max={125}
            step={1}
            value={leverage}
            disabled={levBusy}
            title="Custom leverage"
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 1) changeLeverage(v);
            }}
          />
        </div>
      </div>

      {/* TP / SL toggles */}
      <label className="ticket-toggle-row">
        <input type="checkbox" checked={useTp} onChange={(e) => setUseTp(e.target.checked)} />
        Take profit
      </label>
      {useTp && (
        <div className="ticket-field">
          <input className="input" type="number" value={tp} placeholder="TP price" onChange={(e) => setTp(e.target.value)} />
        </div>
      )}
      <label className="ticket-toggle-row">
        <input type="checkbox" checked={useSl} onChange={(e) => setUseSl(e.target.checked)} />
        Stop loss
      </label>
      {useSl && (
        <div className="ticket-field">
          <input className="input" type="number" value={sl} placeholder="SL price" onChange={(e) => setSl(e.target.value)} />
        </div>
      )}

      {/* info: buying power / margin / est cost / liq */}
      <div className="ticket-info">
        <div className="ticket-info-row">
          <span>Buying power</span>
          <span className="v mono">{fmtUsd(pf?.buying_power)}</span>
        </div>
        <div className="ticket-info-row">
          <span>Margin used</span>
          <span className="v mono">{fmtUsd(pf?.margin_used)}</span>
        </div>
        <div className="ticket-info-row">
          <span>Est. notional</span>
          <span className="v mono">{fmtUsd(estimate.notional)}</span>
        </div>
        <div className="ticket-info-row">
          <span>Est. margin</span>
          <span className="v mono">{fmtUsd(estimate.margin)}</span>
        </div>
        <div className="ticket-info-row">
          <span>Est. liq. price</span>
          <span className="v mono">{estimate.liq ? fmtPrice(estimate.liq) : "—"}</span>
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
    </div>
  );
}
