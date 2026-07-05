// Data model for persisted drawings. Coordinates are stored in *data space*
// ({ time: unix seconds, price }) — NOT pixels — so drawings stay anchored to
// the chart as the user pans/zooms. DrawingLayer converts to pixels at render.

/** A single anchor point in chart data space. */
export interface DrawPoint {
  /** UNIX time in seconds (lightweight-charts Time). */
  time: number;
  /** Price on the value scale. */
  price: number;
}

export type LineStyle = "solid" | "dashed" | "dotted";

export interface Drawing {
  id: string;
  /** Tool id from tools.ts (e.g. "trendline", "fib_retracement"). */
  tool: string;
  /** Anchor points — length depends on the tool (1, 2, or many). */
  points: DrawPoint[];
  /** Stroke color (CSS color string; may reference a theme var via inline). */
  color: string;
  /** Stroke width in px. */
  width: number;
  /** Line dash style. */
  style: LineStyle;
  /** Optional text label (for the "text" tool). */
  text?: string;
}

/** Default appearance for a freshly created drawing. */
export const DEFAULT_COLOR = "#2962ff";
export const DEFAULT_WIDTH = 2;
export const DEFAULT_STYLE: LineStyle = "solid";

/** A palette offered in the style popover (kept theme-aligned). */
export const PALETTE: string[] = [
  "#2962ff", // accent blue
  "#26a69a", // up teal
  "#ef5350", // down red
  "#f7a600", // warn amber
  "#d1d4dc", // text-0
  "#a855f7", // purple
  "#ffffff", // white
];

/** Map our LineStyle to an SVG stroke-dasharray. */
export function dashArray(style: LineStyle, width: number): string | undefined {
  switch (style) {
    case "dashed":
      return `${width * 3} ${width * 2}`;
    case "dotted":
      return `${width} ${width * 2}`;
    default:
      return undefined;
  }
}
