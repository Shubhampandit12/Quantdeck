// Canonical TypeScript types — mirror of backend/quantdeck/core/models.py.
// FROZEN CONTRACT. See docs/API_CONTRACT.md.

export type AssetClass = "us_equity";
export type OrderSide = "buy" | "sell";
export type OrderType = "market" | "limit" | "stop" | "stop_limit";
export type OrderStatus =
  | "pending"
  | "open"
  | "partially_filled"
  | "filled"
  | "cancelled"
  | "rejected"
  | "expired";
export type PositionSide = "long" | "short" | "flat";
export type TimeInForce = "gtc" | "day" | "ioc";
export type DataDelay = "realtime" | "delayed_15m" | "unknown";

export interface SymbolInfo {
  symbol: string;
  name: string;
  asset_class: AssetClass;
  exchange: string;
  provider_symbol: string;
  tradable: boolean;
}

export interface Quote {
  symbol: string;
  asset_class: AssetClass;
  price: number;
  bid?: number | null;
  ask?: number | null;
  volume?: number | null;
  change?: number | null;
  change_percent?: number | null;
  prev_close?: number | null;
  timestamp: string;
  source: string;
  delay: DataDelay;
}

export interface Bar {
  symbol: string;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface OHLCVResponse {
  symbol: string;
  asset_class: AssetClass;
  interval: string;
  bars: Bar[];
  source: string;
  delay: DataDelay;
}

export interface OrderRequest {
  symbol: string;
  side: OrderSide;
  type: OrderType;
  quantity: number;
  limit_price?: number | null;
  stop_price?: number | null;
  take_profit?: number | null;
  stop_loss?: number | null;
  time_in_force?: TimeInForce;
  source?: string;
}

export interface Order {
  id: string;
  portfolio_id: string;
  symbol: string;
  asset_class: AssetClass;
  side: OrderSide;
  type: OrderType;
  quantity: number;
  filled_quantity: number;
  limit_price?: number | null;
  stop_price?: number | null;
  take_profit?: number | null;
  stop_loss?: number | null;
  time_in_force: TimeInForce;
  status: OrderStatus;
  avg_fill_price?: number | null;
  source: string;
  reject_reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Fill {
  id: string;
  order_id: string;
  portfolio_id: string;
  symbol: string;
  side: OrderSide;
  quantity: number;
  price: number;
  commission: number;
  slippage: number;
  timestamp: string;
}

export interface Position {
  symbol: string;
  asset_class: AssetClass;
  side: PositionSide;
  quantity: number;
  avg_entry_price: number;
  last_price: number;
  market_value: number;
  unrealized_pnl: number;
  realized_pnl: number;
  liquidation_price?: number | null;
}

export interface Portfolio {
  id: string;
  name: string;
  base_currency: string;
  starting_balance: number;
  cash: number;
  equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  leverage: number;
  buying_power: number;
  margin_used: number;
  maintenance_margin: number;
  fill_mode: string;
  created_at: string;
  updated_at: string;
}

export interface PortfolioSnapshot {
  portfolio: Portfolio;
  positions: Position[];
  open_orders: Order[];
}

export interface RiskLimits {
  max_leverage: number;
  max_position_notional?: number | null;
  max_order_notional?: number | null;
  max_daily_loss?: number | null;
  allowed_symbols?: string[] | null;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
}

export interface TradeRecord {
  symbol: string;
  side: OrderSide;
  quantity: number;
  entry_price: number;
  exit_price?: number | null;
  entry_time: string;
  exit_time?: string | null;
  pnl: number;
  return_pct: number;
}

export interface BacktestRequest {
  code?: string;
  strategy_id?: string;
  symbols: string[];
  interval: string;
  start?: string;
  end?: string;
  starting_balance: number;
  leverage: number;
  fill_mode: string;
  commission_bps: number;
  slippage_bps: number;
  params?: Record<string, unknown>;
}

export interface BacktestResult {
  starting_balance: number;
  ending_balance: number;
  total_pnl: number;
  total_return_pct: number;
  annualized_return_pct?: number | null;
  max_drawdown_pct: number;
  sharpe_ratio?: number | null;
  win_rate: number;
  num_trades: number;
  profit_factor?: number | null;
  equity_curve: EquityPoint[];
  trades: TradeRecord[];
  logs: string[];
  data_warnings: string[];
  error?: string | null;
}

export interface StrategySummary {
  id: string;
  name: string;
  status: string;
  portfolio_id?: string | null;
}

export interface AITraceStep {
  kind: "thought" | "tool_call" | "tool_result" | "message";
  text?: string;
  tool?: string;
  args?: Record<string, unknown>;
  result?: unknown;
  timestamp: string;
}

export interface MarketEvent {
  type: string;
  portfolio_id?: string | null;
  timestamp: string;
  data: Record<string, unknown>;
}
