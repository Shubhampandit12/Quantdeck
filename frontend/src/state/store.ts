// Global app state (zustand). Holds the active portfolio, selected symbol,
// live quote cache, and derived helpers. Components subscribe to slices.
import { create } from "zustand";
import type { Portfolio, PortfolioSnapshot, Quote } from "../api/types";
import { api } from "../api/client";

interface AppState {
  portfolios: Portfolio[];
  activePortfolioId: string | null;
  snapshot: PortfolioSnapshot | null;
  selectedSymbol: string;
  quotes: Record<string, Quote>;

  // actions
  loadPortfolios: () => Promise<void>;
  setActivePortfolio: (id: string) => Promise<void>;
  refreshSnapshot: () => Promise<void>;
  setSelectedSymbol: (s: string) => void;
  applyQuote: (q: Quote) => void;
}

export const useStore = create<AppState>((set, get) => ({
  portfolios: [],
  activePortfolioId: null,
  snapshot: null,
  selectedSymbol: "BTC/USDT",
  quotes: {},

  loadPortfolios: async () => {
    const portfolios = await api.portfolios();
    set({ portfolios });
    if (!get().activePortfolioId && portfolios.length) {
      await get().setActivePortfolio(portfolios[0].id);
    }
  },

  setActivePortfolio: async (id) => {
    set({ activePortfolioId: id });
    await get().refreshSnapshot();
  },

  refreshSnapshot: async () => {
    const id = get().activePortfolioId;
    if (!id) return;
    const snapshot = await api.snapshot(id);
    set({ snapshot });
  },

  setSelectedSymbol: (s) => set({ selectedSymbol: s }),

  applyQuote: (q) =>
    set((st) => ({ quotes: { ...st.quotes, [q.symbol]: q } })),
}));
