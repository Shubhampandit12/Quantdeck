// ChartPanel — lightweight-charts candlestick chart with live in-place candle
// updates, a data-source/delay badge, and the drawing toolbar + overlay.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  CrosshairMode,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import "./ChartPanel.css";
import { api } from "../../api/client";
import { openMarketSocket } from "../../api/ws";
import type { Bar, DataDelay, Quote } from "../../api/types";
import { fmtPrice } from "../../lib/format";
import DrawingToolbar from "../drawing/DrawingToolbar";
import DrawingLayer from "../drawing/DrawingLayer";
import { useDrawingTool } from "../drawing/toolState";

interface Props {
  symbol: string;
  interval: string;
}

// Seconds per bar for supported intervals — used to bucket live ticks.
const INTERVAL_SECONDS: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "4h": 14400,
  "1d": 86400,
  "1w": 604800,
};

// ISO timestamp -> unix seconds (lightweight-charts Time).
function toUnix(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function barToCandle(b: Bar): CandlestickData {
  return { time: toUnix(b.timestamp), open: b.open, high: b.high, low: b.low, close: b.close };
}

export default function ChartPanel({ symbol, interval }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  // Store chart/series in React state too so the DrawingLayer re-renders once ready.
  const [chart, setChart] = useState<IChartApi | null>(null);
  const [series, setSeries] = useState<ISeriesApi<"Candlestick"> | null>(null);

  // Active drawing tool + clear-all live in a shared store so the (frozen-prop)
  // DrawingLayer can coordinate without extra props.
  const activeTool = useDrawingTool((s) => s.activeTool);
  const setActiveTool = useDrawingTool((s) => s.setActiveTool);
  const requestClearAll = useDrawingTool((s) => s.requestClearAll);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<{ price: number; source: string; delay: DataDelay } | null>(null);

  // The current (rightmost) candle we aggregate live ticks into.
  const lastBarRef = useRef<CandlestickData | null>(null);

  const bucketSec = INTERVAL_SECONDS[interval] ?? 3600;

  // Create the chart once.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const c = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#a3a6af",
        fontFamily: "var(--font-sans)",
      },
      grid: {
        vertLines: { color: "#1c2230" },
        horzLines: { color: "#1c2230" },
      },
      rightPriceScale: { borderColor: "#2a2e39" },
      timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
      // Explicitly enable all mouse-driven navigation so scroll-wheel zoom,
      // drag-to-pan, and pinch all work (defaults can be surprising).
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
        axisPressedMouseMove: true,
        axisDoubleClickReset: true,
      },
    });
    const s = c.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderUpColor: "#26a69a",
      borderDownColor: "#ef5350",
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    chartRef.current = c;
    seriesRef.current = s;
    setChart(c);
    setSeries(s);
    // Expose the chart for e2e verification / debugging (harmless).
    (window as unknown as { __qdChart?: IChartApi }).__qdChart = c;
    return () => {
      c.remove();
      chartRef.current = null;
      seriesRef.current = null;
      setChart(null);
      setSeries(null);
    };
  }, []);

  // Load OHLCV whenever symbol or interval changes; don't recreate the chart.
  useEffect(() => {
    const s = seriesRef.current;
    if (!s) return;
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .ohlcv(symbol, interval, 500)
      .then((resp) => {
        if (!alive) return;
        const candles = resp.bars.map(barToCandle);
        s.setData(candles);
        lastBarRef.current = candles.length ? candles[candles.length - 1] : null;
        setLast({
          price: resp.bars.length ? resp.bars[resp.bars.length - 1].close : 0,
          source: resp.source,
          delay: resp.delay,
        });
        chartRef.current?.timeScale().fitContent();
        setLoading(false);
      })
      .catch((e) => {
        if (!alive) return;
        setError(e?.message ?? "Failed to load chart data");
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [symbol, interval]);

  // Live quotes for the selected symbol; aggregate into the current bar in place.
  useEffect(() => {
    const sock = openMarketSocket([symbol], (q: Quote) => {
      if (q.symbol !== symbol) return;
      const s = seriesRef.current;
      if (!s) return;
      setLast({ price: q.price, source: q.source, delay: q.delay });

      const tSec = Math.floor(new Date(q.timestamp).getTime() / 1000);
      const bucketStart = (Math.floor(tSec / bucketSec) * bucketSec) as UTCTimestamp;
      const cur = lastBarRef.current;

      if (cur && (cur.time as number) === bucketStart) {
        // Update the open bar in place.
        const updated: CandlestickData = {
          time: bucketStart,
          open: cur.open,
          high: Math.max(cur.high, q.price),
          low: Math.min(cur.low, q.price),
          close: q.price,
        };
        lastBarRef.current = updated;
        s.update(updated);
      } else if (!cur || bucketStart > (cur.time as number)) {
        // Start a fresh bar for the new bucket.
        const fresh: CandlestickData = {
          time: bucketStart,
          open: q.price,
          high: q.price,
          low: q.price,
          close: q.price,
        };
        lastBarRef.current = fresh;
        s.update(fresh);
      }
    });
    return () => sock.close();
  }, [symbol, bucketSec]);

  const delayBadge = useMemo(() => {
    if (!last) return null;
    const rt = last.delay === "realtime";
    return (
      <span className={`badge ${rt ? "badge-rt" : "badge-delayed"}`}>
        {last.source} · {rt ? "Realtime" : last.delay === "delayed_15m" ? "15m delayed" : "Delayed"}
      </span>
    );
  }, [last]);

  return (
    <div className="chartpanel">
      <div className="chart-head">
        <span className="sym">{symbol}</span>
        <span className="last mono">{last ? fmtPrice(last.price) : "—"}</span>
        {delayBadge}
      </div>

      <DrawingToolbar activeTool={activeTool} onSelect={setActiveTool} onClearAll={requestClearAll} />

      <div className="chart-stage">
        <div className="chart-mount" ref={containerRef} />
        <DrawingLayer chart={chart} series={series} symbol={symbol} containerRef={containerRef} />
      </div>

      {loading && <div className="chart-loading">Loading {symbol}…</div>}
      {error && <div className="chart-error">{error}</div>}
    </div>
  );
}
