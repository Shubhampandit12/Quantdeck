// BacktestPage — full page with shared top nav + the BacktestPanel.
import { Link, NavLink, useSearchParams } from "react-router-dom";
import BacktestPanel from "../components/BacktestPanel";
import "./BacktestPage.css";

export default function BacktestPage() {
  // Allow pre-seeding the symbol via ?symbol=BTC/USDT (e.g. from the terminal).
  const [params] = useSearchParams();
  const defaultSymbol = params.get("symbol") ?? undefined;

  return (
    <div className="bt-page">
      <header className="bt-nav">
        <Link to="/" className="bt-logo">
          Quant<span>Deck</span>
        </Link>
        <nav className="bt-nav-links">
          <NavLink to="/terminal" className="bt-nav-link">
            Terminal
          </NavLink>
          <NavLink to="/strategies" className="bt-nav-link">
            Strategies
          </NavLink>
          <NavLink to="/backtest" className="bt-nav-link">
            Backtest
          </NavLink>
        </nav>
        <div className="bt-nav-right">
          <span className="badge badge-demo">Paper / Demo Money</span>
        </div>
      </header>

      <main className="bt-main">
        <div className="bt-main-head">
          <h1>Backtest</h1>
          <p className="muted">
            Simulate a strategy over historical data. Demo money only — no real
            capital is at risk.
          </p>
        </div>
        <BacktestPanel defaultSymbol={defaultSymbol} />
      </main>
    </div>
  );
}
