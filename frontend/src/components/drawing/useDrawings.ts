// Drawing state management.
//
// Two pieces live here:
//   1. `useDrawingTool` — a tiny module-level zustand store holding the ACTIVE
//      drawing tool. This is the "shared context" that lets DrawingLayer read
//      the tool that DrawingToolbar sets, WITHOUT changing DrawingLayer's frozen
//      prop signature { chart, series, symbol, containerRef }. ChartPanel wires
//      DrawingToolbar's onSelect -> setTool, and DrawingLayer subscribes via
//      useDrawingTool().
//   2. `useDrawings` — per-symbol CRUD over Drawing[], persisted to localStorage
//      under key `qd:drawings:<symbol>`. Loads when the symbol changes and saves
//      on every mutation.

import { useCallback, useEffect, useRef, useState } from "react";
import type { Drawing } from "./types";

// ---------------------------------------------------------------------------
// Shared active-tool store.
//
// The single source of truth lives in `toolState.ts` (the bridge the terminal
// agent's ChartPanel/DrawingToolbar publish to). We re-export it here so both
// this hook and DrawingLayer coordinate through ONE store instance. The store
// exposes { activeTool, setActiveTool, clearAllToken, requestClearAll }.
// ---------------------------------------------------------------------------
export { useDrawingTool } from "./toolState";

// ---------------------------------------------------------------------------
// Per-symbol persistence
// ---------------------------------------------------------------------------
const keyFor = (symbol: string) => `qd:drawings:${symbol}`;

function loadDrawings(symbol: string): Drawing[] {
  try {
    const raw = localStorage.getItem(keyFor(symbol));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Basic shape validation — tolerate older/foreign data.
    return parsed.filter(
      (d): d is Drawing =>
        d && typeof d.id === "string" && typeof d.tool === "string" && Array.isArray(d.points),
    );
  } catch {
    return [];
  }
}

function saveDrawings(symbol: string, drawings: Drawing[]): void {
  try {
    localStorage.setItem(keyFor(symbol), JSON.stringify(drawings));
  } catch {
    /* localStorage may be full/disabled — ignore */
  }
}

let _idSeq = 0;
export function makeId(): string {
  _idSeq += 1;
  return `dw_${Date.now().toString(36)}_${_idSeq.toString(36)}`;
}

export interface UseDrawings {
  drawings: Drawing[];
  selectedId: string | null;
  add: (d: Drawing) => void;
  update: (id: string, patch: Partial<Drawing>) => void;
  /** Replace the points array of one drawing (used while dragging). */
  updatePoints: (id: string, points: Drawing["points"]) => void;
  remove: (id: string) => void;
  clearAll: () => void;
  select: (id: string | null) => void;
  selected: Drawing | null;
}

/**
 * Manage drawings for a single symbol. Reloads from localStorage whenever
 * `symbol` changes; persists on every change.
 */
export function useDrawings(symbol: string): UseDrawings {
  const [drawings, setDrawings] = useState<Drawing[]>(() => loadDrawings(symbol));
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Track the symbol we last hydrated so we can skip the redundant save that
  // would otherwise fire on the very load that repopulates `drawings`.
  const loadedSymbol = useRef(symbol);

  // Reload when the symbol changes.
  useEffect(() => {
    setDrawings(loadDrawings(symbol));
    setSelectedId(null);
    loadedSymbol.current = symbol;
  }, [symbol]);

  // Persist on change (only for the currently-loaded symbol).
  useEffect(() => {
    if (loadedSymbol.current !== symbol) return;
    saveDrawings(symbol, drawings);
  }, [symbol, drawings]);

  const add = useCallback((d: Drawing) => {
    setDrawings((prev) => [...prev, d]);
    setSelectedId(d.id);
  }, []);

  const update = useCallback((id: string, patch: Partial<Drawing>) => {
    setDrawings((prev) => prev.map((d) => (d.id === id ? { ...d, ...patch } : d)));
  }, []);

  const updatePoints = useCallback((id: string, points: Drawing["points"]) => {
    setDrawings((prev) => prev.map((d) => (d.id === id ? { ...d, points } : d)));
  }, []);

  const remove = useCallback((id: string) => {
    setDrawings((prev) => prev.filter((d) => d.id !== id));
    setSelectedId((cur) => (cur === id ? null : cur));
  }, []);

  const clearAll = useCallback(() => {
    setDrawings([]);
    setSelectedId(null);
  }, []);

  const select = useCallback((id: string | null) => setSelectedId(id), []);

  const selected = drawings.find((d) => d.id === selectedId) ?? null;

  return { drawings, selectedId, add, update, updatePoints, remove, clearAll, select, selected };
}
