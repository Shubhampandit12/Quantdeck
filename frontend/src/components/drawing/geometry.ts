// Pure geometry helpers for the drawing layer: data<->pixel conversion and
// hit-testing. Kept free of React so it stays easy to reason about/test.
import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { DrawPoint } from "./types";

export interface Px {
  x: number;
  y: number;
}

/** Convert a data-space point to pixel coordinates, or null if off-scale. */
export function toPixel(
  chart: IChartApi,
  series: ISeriesApi<"Candlestick">,
  p: DrawPoint,
): Px | null {
  const x = chart.timeScale().timeToCoordinate(p.time as Time);
  const y = series.priceToCoordinate(p.price);
  if (x === null || y === null) return null;
  return { x, y };
}

/**
 * Convert a pixel coordinate back to data space. Uses timeScale/series inverse
 * conversions. If the x maps to no bar time (e.g. right of the last bar), fall
 * back to a linear estimate off the visible logical range so drawings can still
 * be placed in the "future" whitespace.
 */
export function toData(
  chart: IChartApi,
  series: ISeriesApi<"Candlestick">,
  x: number,
  y: number,
): DrawPoint | null {
  const price = series.coordinateToPrice(y);
  if (price === null) return null;

  let time = chart.timeScale().coordinateToTime(x) as number | null;
  if (time === null) {
    time = estimateTimeFromX(chart, x);
  }
  if (time === null) return null;
  return { time, price };
}

/**
 * Estimate a unix time for an x pixel using two known bar coordinates. Lets us
 * anchor drawings placed in the empty space to the right of the last candle.
 */
function estimateTimeFromX(chart: IChartApi, x: number): number | null {
  const ts = chart.timeScale();
  const range = ts.getVisibleRange();
  if (!range) return null;
  const xFrom = ts.timeToCoordinate(range.from);
  const xTo = ts.timeToCoordinate(range.to);
  if (xFrom === null || xTo === null || xTo === xFrom) return null;
  const tFrom = range.from as number;
  const tTo = range.to as number;
  const slope = (tTo - tFrom) / (xTo - xFrom);
  return Math.round(tFrom + (x - xFrom) * slope);
}

/** Distance from point P to segment AB (all in pixels). */
export function distToSegment(p: Px, a: Px, b: Px): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  const projX = a.x + t * dx;
  const projY = a.y + t * dy;
  return Math.hypot(p.x - projX, p.y - projY);
}

/** Distance from point P to a polyline (min over all segments). */
export function distToPolyline(p: Px, pts: Px[]): number {
  if (pts.length === 0) return Infinity;
  if (pts.length === 1) return Math.hypot(p.x - pts[0].x, p.y - pts[0].y);
  let min = Infinity;
  for (let i = 0; i < pts.length - 1; i++) {
    min = Math.min(min, distToSegment(p, pts[i], pts[i + 1]));
  }
  return min;
}

/** Is point P inside (or within `pad`px of) the axis-aligned rect of a,b? */
export function nearRect(p: Px, a: Px, b: Px, pad: number): boolean {
  const minX = Math.min(a.x, b.x) - pad;
  const maxX = Math.max(a.x, b.x) + pad;
  const minY = Math.min(a.y, b.y) - pad;
  const maxY = Math.max(a.y, b.y) + pad;
  if (p.x < minX || p.x > maxX || p.y < minY || p.y > maxY) return false;
  // Treat as "near" if within pad of any edge OR inside the box.
  return true;
}

/** Clamp helper. */
export function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
