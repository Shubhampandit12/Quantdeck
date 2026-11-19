// Typed REST client for the QuantDeck API. One function per endpoint in
// docs/API_CONTRACT.md. All calls go through `req()` which handles JSON + errors.
import type {
  AITraceStep,
  Fill,
  OHLCVResponse,
  Order,
  OrderRequest,
  Portfolio,
  PortfolioSnapshot,
  Position,
  Quote,
  RiskLimits,
  StrategySummary,
  SymbolInfo,
} from "./types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const enc = (s: string) => encodeURIComponent(s);

export const api = {
  // Health
  health: () =>
    req<{ status: string; version: string; ai_enabled: boolean; providers: Record<string, unknown> }>(
      "/health",
    ),

  // Market data
  symbols: (assetClass?: string) =>
    req<SymbolInfo[]>(`/symbols${assetClass ? `?asset_class=${assetClass}` : ""}`),
  search: (q: string, limit = 25) =>
    req<SymbolInfo[]>(`/search?q=${enc(q)}&limit=${limit}`),
  quote: (symbol: string) => req<Quote>(`/quote/${enc(symbol)}`),
  quotes: (symbols: string[]) =>
    req<Quote[]>(`/quotes?symbols=${enc(symbols.join(","))}`),
  ohlcv: (symbol: string, interval = "1d", limit = 500, start?: string, end?: string) => {
    const p = new URLSearchParams({ interval, limit: String(limit) });
    if (start) p.set("start", start);
    if (end) p.set("end", end);
    return req<OHLCVResponse>(`/ohlcv/${enc(symbol)}?${p.toString()}`);
  },
  marketSummary: () =>
    req<{ movers_up: Quote[]; movers_down: Quote[]; stocks: Quote[] }>(
      "/market/summary",
    ),

  // Portfolios
  portfolios: () => req<Portfolio[]>("/portfolios"),
  createPortfolio: (body: { name: string; starting_balance: number; leverage: number; fill_mode?: string }) =>
    req<Portfolio>("/portfolios", { method: "POST", body: JSON.stringify(body) }),
  portfolio: (id: string) => req<Portfolio>(`/portfolios/${id}`),
  snapshot: (id: string) => req<PortfolioSnapshot>(`/portfolios/${id}/snapshot`),
  resetPortfolio: (id: string, starting_balance?: number) =>
    req<Portfolio>(`/portfolios/${id}/reset`, {
      method: "POST",
      body: JSON.stringify({ starting_balance }),
    }),
  setLeverage: (id: string, leverage: number) =>
    req<Portfolio>(`/portfolios/${id}/leverage`, {
      method: "POST",
      body: JSON.stringify({ leverage }),
    }),
  setRisk: (id: string, limits: RiskLimits) =>
    req<Portfolio>(`/portfolios/${id}/risk`, { method: "POST", body: JSON.stringify(limits) }),
  positions: (id: string) => req<Position[]>(`/portfolios/${id}/positions`),
  orders: (id: string, open = false) =>
    req<Order[]>(`/portfolios/${id}/orders${open ? "?open=true" : ""}`),
  fills: (id: string, limit = 200) =>
    req<Fill[]>(`/portfolios/${id}/fills?limit=${limit}`),

  // Orders
  placeOrder: (id: string, order: OrderRequest) =>
    req<Order>(`/portfolios/${id}/orders`, { method: "POST", body: JSON.stringify(order) }),
  cancelOrder: (id: string, orderId: string) =>
    req<Order>(`/portfolios/${id}/orders/${orderId}/cancel`, { method: "POST" }),
  modifyOrder: (id: string, orderId: string, patch: Partial<OrderRequest>) =>
    req<Order>(`/portfolios/${id}/orders/${orderId}/modify`, {
      method: "POST",
      body: JSON.stringify(patch),
    }),
  flatten: (id: string) => req<Order[]>(`/portfolios/${id}/flatten`, { method: "POST" }),

  // Strategies
  strategies: () => req<StrategySummary[]>("/strategies"),
  createStrategy: (body: { name: string; code: string; portfolio_id?: string; params?: Record<string, unknown> }) =>
    req<StrategySummary>("/strategies", { method: "POST", body: JSON.stringify(body) }),
  strategy: (id: string) =>
    req<{ id: string; name: string; code: string; status: string; portfolio_id?: string; params?: Record<string, unknown> }>(
      `/strategies/${id}`,
    ),
  updateStrategy: (id: string, body: { name?: string; code?: string; params?: Record<string, unknown> }) =>
    req<StrategySummary>(`/strategies/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteStrategy: (id: string) => req<{ ok: boolean }>(`/strategies/${id}`, { method: "DELETE" }),
  startStrategy: (id: string, body: { portfolio_id: string; symbols?: string[]; interval?: string }) =>
    req<StrategySummary>(`/strategies/${id}/start`, { method: "POST", body: JSON.stringify(body) }),
  stopStrategy: (id: string) => req<StrategySummary>(`/strategies/${id}/stop`, { method: "POST" }),
  strategyLogs: (id: string, limit = 200) =>
    req<{ logs: string[] }>(`/strategies/${id}/logs?limit=${limit}`),

  // AI
  aiStatus: () => req<{ enabled: boolean; model: string }>("/ai/status"),
  aiChat: (portfolio_id: string, message: string) =>
    req<{ reply: string; trace: AITraceStep[] }>("/ai/chat", {
      method: "POST",
      body: JSON.stringify({ portfolio_id, message }),
    }),
  aiSessionStart: (portfolio_id: string, strategy_prompt: string, guardrails?: Record<string, unknown>) =>
    req<{ session_id: string; status: string }>("/ai/session/start", {
      method: "POST",
      body: JSON.stringify({ portfolio_id, strategy_prompt, guardrails }),
    }),
  aiSessionPause: (sid: string) => req<{ status: string }>(`/ai/session/${sid}/pause`, { method: "POST" }),
  aiSessionResume: (sid: string) => req<{ status: string }>(`/ai/session/${sid}/resume`, { method: "POST" }),
  aiSessionStop: (sid: string) => req<{ status: string }>(`/ai/session/${sid}/stop`, { method: "POST" }),
  aiSession: (sid: string) =>
    req<{ session_id: string; status: string; trace: AITraceStep[] }>(`/ai/session/${sid}`),
};
