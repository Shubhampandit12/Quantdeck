// Registry of all TradingView-style drawing tools. Each entry has a stable id
// (used as Drawing.tool), a human label, a single-char/emoji icon, a category
// for grouping in the toolbar, and how many anchor points it needs to be drawn.
//
// `points` semantics:
//   1  -> single click places the drawing (hline, hray, vline, text)
//   2  -> click-drag defines two anchors (lines, rays, fibs, boxes, positions)
//   -1 -> free-form multi-point (brush, highlighter)

export type ToolCategory =
  | "cursor"
  | "lines"
  | "fibonacci"
  | "shapes"
  | "annotation"
  | "measure"
  | "positions";

export interface ToolDef {
  id: string;
  label: string;
  icon: string;
  category: ToolCategory;
  /** anchor points needed: 0 (cursor), 1, 2, or -1 for free-form */
  points: 0 | 1 | 2 | -1;
}

export const TOOLS: ToolDef[] = [
  // Cursor / selection
  { id: "cursor", label: "Cursor", icon: "⤢", category: "cursor", points: 0 },

  // Lines
  { id: "trendline", label: "Trend Line", icon: "╱", category: "lines", points: 2 },
  { id: "ray", label: "Ray", icon: "➶", category: "lines", points: 2 },
  { id: "extended", label: "Extended Line", icon: "⟷", category: "lines", points: 2 },
  { id: "hline", label: "Horizontal Line", icon: "─", category: "lines", points: 1 },
  { id: "hray", label: "Horizontal Ray", icon: "→", category: "lines", points: 2 },
  { id: "vline", label: "Vertical Line", icon: "│", category: "lines", points: 1 },
  { id: "channel", label: "Parallel Channel", icon: "▤", category: "lines", points: 2 },

  // Fibonacci
  { id: "fib_retracement", label: "Fib Retracement", icon: "𝟛", category: "fibonacci", points: 2 },
  { id: "fib_extension", label: "Fib Extension", icon: "𝟝", category: "fibonacci", points: 2 },
  { id: "fib_fan", label: "Fib Fan", icon: "✳", category: "fibonacci", points: 2 },

  // Shapes
  { id: "rectangle", label: "Rectangle", icon: "▭", category: "shapes", points: 2 },
  { id: "ellipse", label: "Ellipse", icon: "◯", category: "shapes", points: 2 },
  { id: "triangle", label: "Triangle", icon: "△", category: "shapes", points: 2 },

  // Annotation
  { id: "brush", label: "Brush", icon: "✎", category: "annotation", points: -1 },
  { id: "highlighter", label: "Highlighter", icon: "🖍", category: "annotation", points: -1 },
  { id: "arrow", label: "Arrow", icon: "↗", category: "annotation", points: 2 },
  { id: "text", label: "Text", icon: "T", category: "annotation", points: 1 },

  // Measure
  { id: "price_range", label: "Price Range", icon: "↕", category: "measure", points: 2 },
  { id: "date_range", label: "Date Range", icon: "↔", category: "measure", points: 2 },

  // Positions
  { id: "long_position", label: "Long Position", icon: "▲", category: "positions", points: 2 },
  { id: "short_position", label: "Short Position", icon: "▼", category: "positions", points: 2 },
];

/** Ordered category → label for grouping in the toolbar. */
export const CATEGORY_LABELS: Record<ToolCategory, string> = {
  cursor: "Cursor",
  lines: "Lines",
  fibonacci: "Fibonacci",
  shapes: "Shapes",
  annotation: "Annotation",
  measure: "Measure",
  positions: "Positions",
};

export const CATEGORY_ORDER: ToolCategory[] = [
  "cursor",
  "lines",
  "fibonacci",
  "shapes",
  "annotation",
  "measure",
  "positions",
];

/** Quick lookup: tool id -> definition. */
export const TOOL_BY_ID: Record<string, ToolDef> = TOOLS.reduce(
  (acc, t) => {
    acc[t.id] = t;
    return acc;
  },
  {} as Record<string, ToolDef>,
);

/** How many anchor points a tool needs (0 for cursor). */
export function toolPointCount(toolId: string): number {
  return TOOL_BY_ID[toolId]?.points ?? 0;
}

/** Fibonacci retracement + extension levels. */
export const FIB_RETRACEMENT_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
export const FIB_EXTENSION_LEVELS = [...FIB_RETRACEMENT_LEVELS, 1.272, 1.618];
/** Angles (as fib fractions) used for the fib fan spokes. */
export const FIB_FAN_LEVELS = [0.236, 0.382, 0.5, 0.618, 0.786, 1];
