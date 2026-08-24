// TerminalPage — the full trading terminal.
// Owns: selected symbol (from route), timeframe state, and the live event stream
// that keeps the portfolio snapshot fresh and feeds strategy/AI logs to the bottom panel.
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
import CommandBar from "../components/terminal/CommandBar";

export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h" | "1d" | "1w";

// A single line rendered in the bottom panel's log tabs.
export interface LogLine {
  id: number;
  ts: string;
  text: string;
}

let _logId = 0;

export default function TerminalPage() {
  const { symbol } = useParams();
  const navigate = useNavigate();

  const selectedSymbol = useStore((s) => s.selectedSymbol);
  const setSelectedSymbol = useStore((s) => s.setSelectedSymbol);
  const loadPortfolios = useStore((s) => s.loadPortfolios);
  const refreshSnapshot = useStore((s) => s.refreshSnapshot);
  const activePortfolioId = useStore((s) => s.activePortfolioId);

  const [timeframe, setTimeframe] = useState<Timeframe>("1h");
  const [strategyLogs, setStrategyLogs] = useState<LogLine[]>([]);
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

  const appendLog = useCallback((text: string) => {
    setStrategyLogs((prev) => {
      const next = [...prev, { id: _logId++, ts: new Date().toISOString(), text }];
      // Keep the log buffer bounded.
      return next.length > 500 ? next.slice(next.length - 500) : next;
    });
  }, []);

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
      handleEvent(e, debouncedRefresh, appendLog);
    });
    return () => {
      sock.close();
      setConnected(false);
    };
  }, [activePortfolioId, debouncedRefresh, appendLog]);

  const clearLogs = useCallback(() => setStrategyLogs([]), []);

  const centerChart = useMemo(
    () => <ChartPanel symbol={selectedSymbol} interval={timeframe} />,
    [selectedSymbol, timeframe],
  );

  return (
    <div className="terminal">
      <TopBar
        timeframe={timeframe}
        onTimeframe={setTimeframe}
        onSelectSymbol={selectSymbol}
        connected={connected}
      />

      <div className="terminal-body">
        <aside className="terminal-left">
          <Watchlist selected={selectedSymbol} onSelect={selectSymbol} />
        </aside>

        <section className="terminal-center">
          <div className="terminal-center-chart">{centerChart}</div>
          <div className="terminal-center-bottom">
            <BottomPanel strategyLogs={strategyLogs} onClearLogs={clearLogs} />
          </div>
        </section>

        <aside className="terminal-right">
          <OrderTicket symbol={selectedSymbol} timeframe={timeframe} />
          <PortfolioSummary />
        </aside>
      </div>

      <CommandBar />
    </div>
  );
}

// Route an incoming event: refresh snapshot on state-changing events and forward
// strategy/AI text lines to the bottom panel logs.
function handleEvent(
  e: MarketEvent,
  refresh: () => void,
  appendLog: (t: string) => void,
) {
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
  if (t === "strategy_log" || t === "strategy_status") {
    const msg = (e.data?.message ?? e.data?.line ?? e.data?.status) as string | undefined;
    if (msg) appendLog(`[strategy] ${msg}`);
  }
  if (t === "ai_trace" || t === "ai_status") {
    const step = e.data as Record<string, unknown>;
    const text = (step.text ?? step.status ?? step.tool) as string | undefined;
    if (text) appendLog(`[ai] ${text}`);
  }
  if (t === "liquidation") {
    appendLog(`[liquidation] ${JSON.stringify(e.data)}`);
  }
}
