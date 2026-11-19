// HomePage — minimal landing page: project name, one-line description, and
// links into the two main areas of the app.
import { Link } from "react-router-dom";
import "./HomePage.css";

export default function HomePage() {
  return (
    <main className="home">
      <h1 className="home-title">
        Quant<span className="home-title-accent">Deck</span>
      </h1>
      <p className="home-sub">
        Paper-trade US stocks by hand, with an AI agent, or with your own Python
        strategies — demo money only.
      </p>
      <div className="home-actions">
        <Link className="btn btn-primary btn-lg" to="/terminal/AAPL">
          Open Terminal
        </Link>
        <Link className="btn btn-lg" to="/strategies">
          Strategies
        </Link>
      </div>
    </main>
  );
}
