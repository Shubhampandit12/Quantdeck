// TerminalPage — the full trading terminal.
// Owns: selected symbol (from route), timeframe state, and the live event stream
// that keeps the portfolio snapshot fresh (positions/orders/portfolio events).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import "./TerminalPage.css";
import { useStore } from "../state/store";
import { openEventSocket } from "../api/ws";
import type { MarketEvent } from "../api/types";
import TopBar from "../components/terminal/TopBar";
import Watchlist from "../components/terminal/Watchlist";
import ChartPanel from "../components/terminal/ChartPanel";
import OrderTicket from "../components/terminal/OrderTicket";
import PortfolioSummary from "../components/terminal/PortfolioSummary";
import BottomPanel from "../components/terminal/BottomPanel";

export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h" | "1d" | "1w";

export default function TerminalPage() {
  const { symbol } = useParams();
  const navigate = useNavigate();

  const selectedSymbol = useStore((s) => s.selectedSymbol);
  const setSelectedSymbol = useStore((s) => s.setSelectedSymbol);
  const loadPortfolios = useStore((s) => s.loadPortfolios);
  const refreshSnapshot = useStore((s) => s.refreshSnapshot);
  const activePortfolioId = useStore((s) => s.activePortfolioId);

  const [timeframe, setTimeframe] = useState<Timeframe>("1h");
  const [connected, setConnected] = useState(false);

  // Decode the route symbol and sync it into the store.
  useEffect(() => {
    if (symbol) {
      const decoded = decodeURIComponent(symbol);
      if (decoded !== selectedSymbol) setSelectedSymbol(decoded);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  // Load portfolios once on mount.
  useEffect(() => {
    void loadPortfolios();
  }, [loadPortfolios]);

  // Navigate helper used by watchlist/search: change route + store symbol.
  const selectSymbol = useCallback(
    (sym: string) => {
      setSelectedSymbol(sym);
      navigate(`/terminal/${encodeURIComponent(sym)}`);
    },
    [navigate, setSelectedSymbol],
  );

  // Debounced snapshot refresh so a burst of events triggers a single fetch.
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debouncedRefresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      void refreshSnapshot();
    }, 250);
  }, [refreshSnapshot]);

  // Subscribe to the engine/strategy/AI event stream for the active portfolio.
  useEffect(() => {
    if (!activePortfolioId) return;
    setConnected(false);
    const sock = openEventSocket(activePortfolioId, (e: MarketEvent) => {
      setConnected(true);
      handleEvent(e, debouncedRefresh);
    });
    return () => {
      sock.close();
      setConnected(false);
    };
  }, [activePortfolioId, debouncedRefresh]);

  const centerChart = useMemo(
    () => <ChartPanel symbol={selectedSymbol} interval={timeframe} />,
    [selectedSymbol, timeframe],
  );

  return (
    <div className="terminal">
      <TopBar timeframe={timeframe} onTimeframe={setTimeframe} connected={connected} />

      <div className="terminal-body">
        <aside className="terminal-left">
          <Watchlist selected={selectedSymbol} onSelect={selectSymbol} />
        </aside>

        <section className="terminal-center">
          <div className="terminal-center-chart">{centerChart}</div>
          <div className="terminal-center-bottom">
            <BottomPanel />
          </div>
        </section>

        <aside className="terminal-right">
          <OrderTicket symbol={selectedSymbol} timeframe={timeframe} />
          <PortfolioSummary />
        </aside>
      </div>
    </div>
  );
}

// Route an incoming event: refresh the snapshot on any state-changing event
// so Positions/Open Orders/balances stay live.
function handleEvent(e: MarketEvent, refresh: () => void) {
  const t = e.type;
  if (
    t === "order" ||
    t === "position" ||
    t === "portfolio" ||
    t === "margin" ||
    t === "liquidation" ||
    t === "fill"
  ) {
    refresh();
  }
}
