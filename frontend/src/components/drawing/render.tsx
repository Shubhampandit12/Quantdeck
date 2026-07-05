// Pure SVG renderers for each drawing tool. Given a drawing plus a data->pixel
// converter and the current viewport size, each function returns SVG elements.
// Keeping this separate from DrawingLayer keeps the interaction logic readable.
import React from "react";
import type { Drawing, DrawPoint } from "./types";
import { dashArray } from "./types";
import {
  FIB_RETRACEMENT_LEVELS,
  FIB_EXTENSION_LEVELS,
  FIB_FAN_LEVELS,
} from "./tools";
import { extendLine, type Px } from "./geometry";

export interface RenderCtx {
  /** Convert a data point to pixels; null if currently off-scale. */
  toPx: (p: DrawPoint) => Px | null;
  /** Convert a price to a y pixel (for horizontal lines spanning full width). */
  priceToY: (price: number) => number | null;
  /** Convert a time to an x pixel (for vertical lines spanning full height). */
  timeToX: (time: number) => number | null;
  width: number;
  height: number;
  selected: boolean;
}

const LABEL_BG = "var(--bg-2)";
const LABEL_FG = "var(--text-0)";
const UP = "var(--up)";
const DOWN = "var(--down)";

/** Common stroke props for a drawing. */
function strokeProps(d: Drawing) {
  const dash = dashArray(d.style, d.width);
  return {
    stroke: d.color,
    strokeWidth: d.width,
    strokeDasharray: dash,
    fill: "none",
  } as const;
}

/** A tiny text label with a translucent background box. */
function Label(props: { x: number; y: number; text: string; color?: string; anchor?: "start" | "middle" | "end" }) {
  const { x, y, text, color = LABEL_FG, anchor = "start" } = props;
  return (
    <text
      x={x}
      y={y}
      fill={color}
      fontSize={11}
      fontFamily="var(--font-mono)"
      textAnchor={anchor}
      dominantBaseline="middle"
      style={{ paintOrder: "stroke", stroke: "var(--bg-0)", strokeWidth: 3, strokeLinejoin: "round" }}
    >
      {text}
    </text>
  );
}


/** Selection handles drawn at each anchor when the drawing is selected. */
function Handles({ pts }: { pts: Px[] }) {
  return (
    <>
      {pts.map((p, i) => (
        <circle
          key={i}
          className="qd-handle"
          cx={p.x}
          cy={p.y}
          r={4}
          fill="var(--accent)"
          stroke="#fff"
          strokeWidth={1}
        />
      ))}
    </>
  );
}

/**
 * Render one drawing to SVG. Returns null when the drawing can't be placed
 * (e.g. all anchors are currently off-scale).
 */
export function renderDrawing(d: Drawing, ctx: RenderCtx): React.ReactNode {
  const { toPx, priceToY, timeToX, width: W, height: H, selected } = ctx;
  const sp = strokeProps(d);

  // Resolve anchors to pixels (some tools tolerate off-scale anchors).
  const px = d.points.map(toPx);

  switch (d.tool) {
    // ---- Simple two-point lines -----------------------------------------
    case "trendline": {
      const [a, b] = px;
      if (!a || !b) return null;
      return (
        <g>
          <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} {...sp} />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }
    case "ray": {
      const [a, b] = px;
      if (!a || !b) return null;
      const [, end] = extendLine(a, b, W, H, false);
      return (
        <g>
          <line x1={a.x} y1={a.y} x2={end.x} y2={end.y} {...sp} />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }
    case "extended": {
      const [a, b] = px;
      if (!a || !b) return null;
      const [s, e] = extendLine(a, b, W, H, true);
      return (
        <g>
          <line x1={s.x} y1={s.y} x2={e.x} y2={e.y} {...sp} />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }

    // ---- Horizontal / vertical ------------------------------------------
    case "hline": {
      const y = priceToY(d.points[0]?.price ?? NaN);
      if (y === null) return null;
      const a = px[0];
      return (
        <g>
          <line x1={0} y1={y} x2={W} y2={y} {...sp} />
          <Label x={W - 6} y={y - 8} text={d.points[0].price.toString()} anchor="end" color={d.color} />
          {selected && a && <Handles pts={[a]} />}
        </g>
      );
    }
    case "hray": {
      const [a, b] = px;
      if (!a) return null;
      const y = a.y;
      const endX = b ? Math.max(a.x, b.x) : W;
      const startX = b && b.x < a.x ? b.x : a.x;
      // Ray extends from the first anchor toward the second anchor's side.
      const goRight = !b || b.x >= a.x;
      return (
        <g>
          <line x1={goRight ? a.x : startX} y1={y} x2={goRight ? endX : a.x} y2={y} {...sp} />
          {selected && <Handles pts={b ? [a, b] : [a]} />}
        </g>
      );
    }
    case "vline": {
      const x = timeToX(d.points[0]?.time ?? NaN);
      if (x === null) return null;
      const a = px[0];
      return (
        <g>
          <line x1={x} y1={0} x2={x} y2={H} {...sp} />
          {selected && a && <Handles pts={[{ x, y: a.y }]} />}
        </g>
      );
    }

    // ---- Parallel channel: two parallel lines + fill ---------------------
    case "channel": {
      const [a, b] = px;
      if (!a || !b) return null;
      // Offset the second parallel line by the vertical distance encoded in a
      // 3rd point if present, else a default offset of 40px.
      const c = px[2];
      const offset = c ? c.y - a.y : 40;
      const a2 = { x: a.x, y: a.y + offset };
      const b2 = { x: b.x, y: b.y + offset };
      return (
        <g>
          <polygon
            points={`${a.x},${a.y} ${b.x},${b.y} ${b2.x},${b2.y} ${a2.x},${a2.y}`}
            fill={d.color}
            fillOpacity={0.08}
            stroke="none"
          />
          <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} {...sp} />
          <line x1={a2.x} y1={a2.y} x2={b2.x} y2={b2.y} {...sp} />
          {selected && <Handles pts={c ? [a, b, c] : [a, b]} />}
        </g>
      );
    }

    // ---- Fibonacci -------------------------------------------------------
    case "fib_retracement":
    case "fib_extension": {
      const [a, b] = px;
      if (!a || !b) return null;
      const levels = d.tool === "fib_extension" ? FIB_EXTENSION_LEVELS : FIB_RETRACEMENT_LEVELS;
      const p0 = d.points[0].price;
      const p1 = d.points[1].price;
      const left = Math.min(a.x, b.x);
      const right = Math.max(a.x, b.x);
      return (
        <g>
          {levels.map((lv) => {
            const price = p0 + (p1 - p0) * lv;
            const y = priceToY(price);
            if (y === null) return null;
            return (
              <g key={lv}>
                <line x1={left} y1={y} x2={right} y2={y} stroke={d.color} strokeWidth={1} strokeDasharray={dashArray(d.style, d.width)} />
                <Label x={left + 4} y={y - 7} text={`${lv.toFixed(3)} (${price.toFixed(2)})`} color={d.color} />
              </g>
            );
          })}
          <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={d.color} strokeWidth={1} strokeOpacity={0.5} />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }
    case "fib_fan": {
      const [a, b] = px;
      if (!a || !b) return null;
      // Spokes from origin (a) toward vertical fib fractions of the b-height.
      const dyTotal = b.y - a.y;
      const [, far] = extendLine(a, b, W, H, false);
      const dirX = far.x - a.x;
      return (
        <g>
          <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={d.color} strokeWidth={1} strokeOpacity={0.5} />
          {FIB_FAN_LEVELS.map((lv) => {
            const ty = a.y + dyTotal * lv;
            const dir = { x: a.x + dirX, y: ty };
            const [, end] = extendLine(a, dir, W, H, false);
            return (
              <line key={lv} x1={a.x} y1={a.y} x2={end.x} y2={end.y} stroke={d.color} strokeWidth={1} strokeDasharray={dashArray(d.style, d.width)} />
            );
          })}
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }

    // ---- Shapes ----------------------------------------------------------
    case "rectangle": {
      const [a, b] = px;
      if (!a || !b) return null;
      const x = Math.min(a.x, b.x);
      const y = Math.min(a.y, b.y);
      const w = Math.abs(b.x - a.x);
      const h = Math.abs(b.y - a.y);
      return (
        <g>
          <rect x={x} y={y} width={w} height={h} {...sp} fill={d.color} fillOpacity={0.08} />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }
    case "ellipse": {
      const [a, b] = px;
      if (!a || !b) return null;
      const cx = (a.x + b.x) / 2;
      const cy = (a.y + b.y) / 2;
      const rx = Math.abs(b.x - a.x) / 2;
      const ry = Math.abs(b.y - a.y) / 2;
      return (
        <g>
          <ellipse cx={cx} cy={cy} rx={rx} ry={ry} {...sp} fill={d.color} fillOpacity={0.08} />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }
    case "triangle": {
      const [a, b] = px;
      if (!a || !b) return null;
      // Right-triangle style: apex at a, base spanning to b.
      const apex = { x: (a.x + b.x) / 2, y: a.y };
      return (
        <g>
          <polygon
            points={`${apex.x},${apex.y} ${a.x},${b.y} ${b.x},${b.y}`}
            {...sp}
            fill={d.color}
            fillOpacity={0.08}
          />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }

    // ---- Free-form -------------------------------------------------------
    case "brush":
    case "highlighter": {
      const pts = px.filter((p): p is Px => p !== null);
      if (pts.length < 2) return null;
      const pointsStr = pts.map((p) => `${p.x},${p.y}`).join(" ");
      const isHi = d.tool === "highlighter";
      return (
        <g>
          <polyline
            points={pointsStr}
            fill="none"
            stroke={d.color}
            strokeWidth={isHi ? d.width * 5 : d.width}
            strokeOpacity={isHi ? 0.3 : 1}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={isHi ? undefined : dashArray(d.style, d.width)}
          />
          {selected && <Handles pts={[pts[0], pts[pts.length - 1]]} />}
        </g>
      );
    }

    // ---- Arrow -----------------------------------------------------------
    case "arrow": {
      const [a, b] = px;
      if (!a || !b) return null;
      const angle = Math.atan2(b.y - a.y, b.x - a.x);
      const headLen = 10 + d.width * 2;
      const h1 = {
        x: b.x - headLen * Math.cos(angle - Math.PI / 6),
        y: b.y - headLen * Math.sin(angle - Math.PI / 6),
      };
      const h2 = {
        x: b.x - headLen * Math.cos(angle + Math.PI / 6),
        y: b.y - headLen * Math.sin(angle + Math.PI / 6),
      };
      return (
        <g>
          <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} {...sp} />
          <polygon points={`${b.x},${b.y} ${h1.x},${h1.y} ${h2.x},${h2.y}`} fill={d.color} stroke="none" />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }

    // ---- Measurement boxes ----------------------------------------------
    case "price_range": {
      const [a, b] = px;
      if (!a || !b) return null;
      const p0 = d.points[0].price;
      const p1 = d.points[1].price;
      const delta = p1 - p0;
      const pct = p0 !== 0 ? (delta / p0) * 100 : 0;
      const x = Math.min(a.x, b.x);
      const y = Math.min(a.y, b.y);
      const w = Math.abs(b.x - a.x) || 80;
      const h = Math.abs(b.y - a.y);
      const up = delta >= 0;
      return (
        <g>
          <rect x={x} y={y} width={w} height={h} fill={up ? UP : DOWN} fillOpacity={0.12} stroke={up ? UP : DOWN} strokeWidth={1} />
          <Label
            x={x + w / 2}
            y={y + h / 2}
            anchor="middle"
            text={`${delta >= 0 ? "+" : ""}${delta.toFixed(2)}  (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)`}
            color={up ? UP : DOWN}
          />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }
    case "date_range": {
      const [a, b] = px;
      if (!a || !b) return null;
      const secs = Math.abs(d.points[1].time - d.points[0].time);
      const days = secs / 86400;
      const x = Math.min(a.x, b.x);
      const w = Math.abs(b.x - a.x);
      const y = Math.min(a.y, b.y);
      const h = Math.abs(b.y - a.y) || 60;
      const label = days >= 1 ? `${days.toFixed(1)}d` : `${(secs / 3600).toFixed(1)}h`;
      return (
        <g>
          <rect x={x} y={y} width={w} height={h} fill="var(--accent)" fillOpacity={0.1} stroke="var(--accent)" strokeWidth={1} />
          <Label x={x + w / 2} y={y + h / 2} anchor="middle" text={label} color="var(--accent)" />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }

    // ---- Position tools --------------------------------------------------
    case "long_position":
    case "short_position": {
      const [a, b] = px;
      if (!a || !b) return null;
      const isLong = d.tool === "long_position";
      const entryPrice = d.points[0].price;
      const secondPrice = d.points[1].price;
      // Target = in profit direction, Stop = opposite. For a long, target is
      // above entry; risk box below. We derive target/stop from |Δ|.
      const dist = Math.abs(secondPrice - entryPrice);
      const targetPrice = isLong ? entryPrice + dist : entryPrice - dist;
      const stopPrice = isLong ? entryPrice - dist : entryPrice + dist;
      const yEntry = a.y;
      const yTarget = priceToY(targetPrice);
      const yStop = priceToY(stopPrice);
      if (yTarget === null || yStop === null) return null;
      const left = Math.min(a.x, b.x);
      const right = Math.max(a.x, b.x) || left + 100;
      const w = Math.max(right - left, 60);
      const rr = dist !== 0 ? (Math.abs(targetPrice - entryPrice) / dist).toFixed(2) : "0";
      return (
        <g>
          {/* Profit (target) zone */}
          <rect x={left} y={Math.min(yEntry, yTarget)} width={w} height={Math.abs(yTarget - yEntry)} fill={UP} fillOpacity={0.14} stroke={UP} strokeWidth={1} />
          {/* Risk (stop) zone */}
          <rect x={left} y={Math.min(yEntry, yStop)} width={w} height={Math.abs(yStop - yEntry)} fill={DOWN} fillOpacity={0.14} stroke={DOWN} strokeWidth={1} />
          {/* Entry line */}
          <line x1={left} y1={yEntry} x2={left + w} y2={yEntry} stroke={d.color} strokeWidth={1.5} />
          <Label x={left + 4} y={yEntry - 8} text={`${isLong ? "LONG" : "SHORT"} entry ${entryPrice.toFixed(2)}`} color={d.color} />
          <Label x={left + 4} y={yTarget + (yTarget < yEntry ? -8 : 12)} text={`Target ${targetPrice.toFixed(2)}`} color={UP} />
          <Label x={left + 4} y={yStop + (yStop < yEntry ? -8 : 12)} text={`Stop ${stopPrice.toFixed(2)}  R:R ${rr}`} color={DOWN} />
          {selected && <Handles pts={[a, b]} />}
        </g>
      );
    }

    // ---- Text ------------------------------------------------------------
    case "text": {
      const a = px[0];
      if (!a) return null;
      const label = d.text && d.text.length ? d.text : "Text";
      return (
        <g>
          <rect
            x={a.x - 2}
            y={a.y - 10}
            width={Math.max(20, label.length * 7 + 8)}
            height={20}
            fill={LABEL_BG}
            fillOpacity={0.85}
            stroke={selected ? "var(--accent)" : "transparent"}
            strokeWidth={1}
            rx={3}
          />
          <text x={a.x + 3} y={a.y} fill={d.color} fontSize={13} fontFamily="var(--font-sans)" dominantBaseline="middle">
            {label}
          </text>
          {selected && <Handles pts={[a]} />}
        </g>
      );
    }

    default:
      return null;
  }
}
