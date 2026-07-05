// WebSocket helpers with auto-reconnect for market quotes and engine events.
import type { MarketEvent, Quote } from "./types";

type Json = Record<string, unknown>;

function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

class ReconnectingSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private backoff = 500;
  constructor(
    private path: string,
    private onMessage: (msg: Json) => void,
    private onOpen?: (ws: WebSocket) => void,
  ) {
    this.connect();
  }
  private connect() {
    if (this.closed) return;
    const ws = new WebSocket(wsUrl(this.path));
    this.ws = ws;
    ws.onopen = () => {
      this.backoff = 500;
      this.onOpen?.(ws);
    };
    ws.onmessage = (ev) => {
      try {
        this.onMessage(JSON.parse(ev.data));
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      if (this.closed) return;
      setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 10_000);
    };
    ws.onerror = () => ws.close();
  }
  send(obj: Json) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }
  close() {
    this.closed = true;
    this.ws?.close();
  }
}

/** Subscribe to live quotes for a set of symbols. Returns a controller. */
export function openMarketSocket(
  symbols: string[],
  onQuote: (q: Quote) => void,
): { setSymbols: (s: string[]) => void; close: () => void } {
  let current = new Set(symbols);
  const qs = symbols.length ? `?symbols=${encodeURIComponent(symbols.join(","))}` : "";
  const sock = new ReconnectingSocket(
    `/ws/market${qs}`,
    (msg) => {
      if (msg.type === "quote" && msg.data) onQuote(msg.data as Quote);
    },
    (ws) => {
      if (current.size) ws.send(JSON.stringify({ action: "subscribe", symbols: [...current] }));
    },
  );
  return {
    setSymbols: (s: string[]) => {
      const next = new Set(s);
      const add = [...next].filter((x) => !current.has(x));
      const rem = [...current].filter((x) => !next.has(x));
      if (add.length) sock.send({ action: "subscribe", symbols: add });
      if (rem.length) sock.send({ action: "unsubscribe", symbols: rem });
      current = next;
    },
    close: () => sock.close(),
  };
}

/** Subscribe to engine/strategy/AI events for a portfolio. */
export function openEventSocket(
  portfolioId: string,
  onEvent: (e: MarketEvent) => void,
): { close: () => void } {
  const sock = new ReconnectingSocket(
    `/ws/events?portfolio_id=${encodeURIComponent(portfolioId)}`,
    (msg) => onEvent(msg as unknown as MarketEvent),
  );
  return { close: () => sock.close() };
}
