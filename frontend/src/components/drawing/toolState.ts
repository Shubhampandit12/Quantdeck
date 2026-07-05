// Shared drawing-tool state so ChartPanel/DrawingToolbar (terminal-owned) and
// DrawingLayer (drawing-owned) can coordinate WITHOUT changing the frozen
// DrawingLayer props ({ chart, series, symbol, containerRef }).
//
// The frozen DrawingLayer contract does not receive the active tool as a prop, so
// this tiny pub/sub bridge carries the current tool + a "clear all" signal.
// DrawingLayer may subscribe here; the terminal publishes here.
import { create } from "zustand";

interface DrawingToolState {
  activeTool: string;
  clearAllToken: number; // bumped to request "remove all drawings"
  setActiveTool: (t: string) => void;
  requestClearAll: () => void;
}

export const useDrawingTool = create<DrawingToolState>((set) => ({
  activeTool: "cursor",
  clearAllToken: 0,
  setActiveTool: (t) => set({ activeTool: t }),
  requestClearAll: () => set((s) => ({ clearAllToken: s.clearAllToken + 1 })),
}));
