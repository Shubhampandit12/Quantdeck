// PortfolioSummary — compact cash / equity / uPnL / realized / leverage / margin.
import "./PortfolioSummary.css";
import { useStore } from "../../state/store";
import { fmtUsd, fmtSigned, signClass } from "../../lib/format";

export default function PortfolioSummary() {
  const pf = useStore((s) => s.snapshot?.portfolio);

  return (
    <div className="pfsummary">
      <div className="panel-title">Portfolio</div>
      <div className="pfsummary-grid">
        <Stat label="Cash" value={fmtUsd(pf?.cash)} />
        <Stat label="Equity" value={fmtUsd(pf?.equity)} />
        <Stat
          label="Unrealized P&L"
          value={pf ? fmtSigned(pf.unrealized_pnl) : "—"}
          cls={signClass(pf?.unrealized_pnl)}
        />
        <Stat
          label="Realized P&L"
          value={pf ? fmtSigned(pf.realized_pnl) : "—"}
          cls={signClass(pf?.realized_pnl)}
        />
        <Stat label="Leverage" value={pf ? `${pf.leverage}x` : "—"} />
        <Stat label="Margin used" value={fmtUsd(pf?.margin_used)} />
        <Stat label="Maint. margin" value={fmtUsd(pf?.maintenance_margin)} />
        <Stat label="Buying power" value={fmtUsd(pf?.buying_power)} />
      </div>
    </div>
  );
}

function Stat({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="pfstat">
      <span className="pfstat-lbl">{label}</span>
      <span className={`pfstat-val mono ${cls ?? ""}`}>{value}</span>
    </div>
  );
}
