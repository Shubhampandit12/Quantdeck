// StrategiesPage — full-page host for the StrategyManager, with the shared
// top nav (logo, links, PAPER/DEMO badge). Loads the single demo portfolio on
// mount and runs strategies against it.
import { useEffect } from "react";
import { Link, NavLink } from "react-router-dom";
import { useStore } from "../state/store";
import StrategyManager from "../components/StrategyManager";
import "./StrategiesPage.css";

export default function StrategiesPage() {
  const activePortfolioId = useStore((s) => s.activePortfolioId);
  const loadPortfolios = useStore((s) => s.loadPortfolios);

  // Load portfolios once on mount so we have an active portfolio to run against.
  useEffect(() => {
    void loadPortfolios();
  }, [loadPortfolios]);

  return (
    <div className="strat-page">
      {/* Shared top nav (mirrors the home nav style) */}
      <nav className="qd-nav">
        <Link to="/" className="qd-logo">
          Quant<span className="qd-logo-accent">Deck</span>
        </Link>
        <div className="qd-nav-links">
          <NavLink to="/terminal/AAPL" className="qd-nav-link">
            Charts
          </NavLink>
          <NavLink to="/strategies" className="qd-nav-link">
            Strategies
          </NavLink>
        </div>
        <div className="qd-nav-right">
          <span className="badge badge-demo qd-demo-badge">Paper / Demo Money</span>
        </div>
      </nav>

      {/* Page header */}
      <header className="strat-page-head">
        <h1 className="strat-page-title">Strategy Manager</h1>
      </header>

      {/* Manager body */}
      <main className="strat-page-body">
        {activePortfolioId ? (
          <StrategyManager portfolioId={activePortfolioId} />
        ) : (
          <div className="strat-page-nopf card">
            <p>No active paper portfolio yet.</p>
            <Link to="/terminal/AAPL" className="btn btn-primary">
              Go to the terminal to create one
            </Link>
          </div>
        )}
      </main>
    </div>
  );
}
