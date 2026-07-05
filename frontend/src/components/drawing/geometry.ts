// Pure geometry helpers for the drawing layer: data<->pixel conversion and
// hit-testing. Kept free of React so it stays easy to reason about/test.
//
// IMPORTANT — timeframe/zoom independence: a drawing is anchored to an absolute
// timestamp (unix seconds). lightweight-charts' `timeToCoordinate()` only
// returns a pixel when that EXACT timestamp is a bar on the *current* series —
// so after switching 1h→1d (or panning so the point scrolls off), it returns
// null and the drawing would vanish. To keep drawings pinned to their real
// time at ANY timeframe/scale, we instead project time→x by LINEAR
// INTERPOLATION across the visible time range (time is monotonic in x), which
// is defined for every timestamp, on-scale or off. Price uses the same idea via
// two reference prices when the point is outside the visible price range.
import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { DrawPoint } from "./types";

export interface Px {
  x: number;
  y: number;
}

/** Project a timestamp (unix seconds) to an x pixel for the current view.
 *  Works for any time, not just bar times, by interpolating the visible range. */
export function timeToX(chart: IChartApi, time: number): number | null {
  const ts = chart.timeScale();
  // Fast path: an exact bar time.
  const direct = ts.timeToCoordinate(time as Time);
  if (direct !== null) return direct;

  // Interpolate/extrapolate off the two ends of the visible time range.
  const range = ts.getVisibleRange();
  if (!range) return null;
  const tFrom = range.from as number;
  const tTo = range.to as number;
  const xFrom = ts.timeToCoordinate(range.from);
  const xTo = ts.timeToCoordinate(range.to);
  if (xFrom === null || xTo === null || tTo === tFrom) return null;
  const slope = (xTo - xFrom) / (tTo - tFrom);
  return xFrom + (time - tFrom) * slope;
}

/** Project an x pixel back to a timestamp (unix seconds), for any x. */
export function xToTime(chart: IChartApi, x: number): number | null {
  const ts = chart.timeScale();
  const direct = ts.coordinateToTime(x) as number | null;
  if (direct !== null) return direct;

  const range = ts.getVisibleRange();
  if (!range) return null;
  const tFrom = range.from as number;
  const tTo = range.to as number;
  const xFrom = ts.timeToCoordinate(range.from);
  const xTo = ts.timeToCoordinate(range.to);
  if (xFrom === null || xTo === null || xTo === xFrom) return null;
  const slope = (tTo - tFrom) / (xTo - xFrom);
  return Math.round(tFrom + (x - xFrom) * slope);
}

/** Convert a data-space point to pixel coordinates.
 *  Returns null only if the chart has no usable scale yet. */
export function toPixel(
  chart: IChartApi,
  series: ISeriesApi<"Candlestick">,
  p: DrawPoint,
): Px | null {
  const x = timeToX(chart, p.time);
  const y = series.priceToCoordinate(p.price);
  if (x === null || y === null) return null;
  return { x, y };
}

/**
 * Convert a pixel coordinate back to data space. Uses timeScale/series inverse
 * conversions, falling back to interpolation off the visible range so drawings
 * can be placed anywhere (including the empty space right of the last candle).
 */
export function toData(
  chart: IChartApi,
  series: ISeriesApi<"Candlestick">,
  x: number,
  y: number,
): DrawPoint | null {
  const price = series.coordinateToPrice(y);
  if (price === null) return null;
  const time = xToTime(chart, x);
  if (time === null) return null;
  return { time, price };
}

/** Extend segment a->b to (roughly) the chart edges. Returns [start, end].
 *  `both=false` extends only past b (a ray); `both=true` extends both ways.
 *  Shared by the renderer and the hit-tester so their geometry matches. */
export function extendLine(a: Px, b: Px, w: number, h: number, both = false): [Px, Px] {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (dx === 0 && dy === 0) return [a, b];
  const far = Math.max(w, h) * 4;
  const len = Math.hypot(dx, dy);
  const ux = dx / len;
  const uy = dy / len;
  const forward: Px = { x: b.x + ux * far, y: b.y + uy * far };
  const backward: Px = { x: a.x - ux * far, y: a.y - uy * far };
  return both ? [backward, forward] : [a, forward];
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
