// StrategiesPage — full-page host for the StrategyManager, with the shared
// top nav (logo, links, PAPER/DEMO badge). Uses the active portfolio from the
// global store and ensures portfolios are loaded on mount.
import { useEffect } from "react";
import { Link, NavLink } from "react-router-dom";
import { useStore } from "../state/store";
import StrategyManager from "../components/StrategyManager";
import "./StrategiesPage.css";

export default function StrategiesPage() {
  const portfolios = useStore((s) => s.portfolios);
  const activePortfolioId = useStore((s) => s.activePortfolioId);
  const loadPortfolios = useStore((s) => s.loadPortfolios);
  const setActivePortfolio = useStore((s) => s.setActivePortfolio);

  // Load portfolios once on mount so we have an active portfolio to run against.
  useEffect(() => {
    void loadPortfolios();
  }, [loadPortfolios]);

  const active = portfolios.find((p) => p.id === activePortfolioId) ?? null;

  return (
    <div className="strat-page">
      {/* Shared top nav (mirrors the home nav style) */}
      <nav className="qd-nav">
        <Link to="/" className="qd-logo">
          Quant<span className="qd-logo-accent">Deck</span>
        </Link>
        <div className="qd-nav-links">
          <NavLink to="/" end className="qd-nav-link">
            Markets
          </NavLink>
          <NavLink to="/terminal/AAPL" className="qd-nav-link">
            Charts
          </NavLink>
          <NavLink to="/strategies" className="qd-nav-link">
            Strategies
          </NavLink>
        </div>
        <div className="qd-nav-right">
          {/* Portfolio switcher: pick which paper portfolio strategies run on. */}
          <select
            className="input qd-portfolio-select"
            value={activePortfolioId ?? ""}
            onChange={(e) => void setActivePortfolio(e.target.value)}
            disabled={portfolios.length === 0}
          >
            {portfolios.length === 0 && <option value="">No portfolios</option>}
            {portfolios.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <span className="badge badge-demo">Paper / Demo Money</span>
        </div>
      </nav>

      {/* Page header */}
      <header className="strat-page-head">
        <div>
          <h1 className="strat-page-title">Strategy Manager</h1>
          <p className="strat-page-sub muted">
            Author code once, then run it live on paper money.
            {active ? (
              <>
                {" "}
                Running against <strong>{active.name}</strong>.
              </>
            ) : (
              " Create a portfolio in the terminal to run strategies live."
            )}
          </p>
        </div>
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
