// Absolutely-positioned SVG overlay aligned to the lightweight-charts pane.
//
// Interaction model (TradingView-like):
//   - DRAW a 2-point tool (trend line, ray, rectangle, fib, …): CLICK to drop
//     the first anchor, MOVE the mouse to see a live preview, CLICK again to
//     fix the second anchor. Esc cancels an in-progress draw.
//   - 1-point tools (horizontal/vertical line, text): a single click places it.
//   - Free-form (brush/highlighter): press-drag-release.
//   - CURSOR mode: click a drawing to SELECT it and open a small popover (change
//     color, width, style, or delete). Drag a drawing/anchor to move it.
//     Delete/Backspace removes the selection.
//
// Drawings are CLIPPED to the price plot area so lines never cross into the
// right-hand price axis or the bottom time axis.
//
// The active tool is read from the shared `useDrawingTool` store, keeping this
// component's frozen prop signature { chart, series, symbol, containerRef }.
import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import {
  DEFAULT_COLOR,
  DEFAULT_STYLE,
  DEFAULT_WIDTH,
  PALETTE,
  type Drawing,
  type DrawPoint,
  type LineStyle,
} from "./types";
import { toolPointCount } from "./tools";
import { renderDrawing, type RenderCtx } from "./render";
import {
  distToPolyline,
  distToSegment,
  nearRect,
  toData,
  toPixel,
  type Px,
} from "./geometry";
import { makeId, useDrawings } from "./useDrawings";
import { useDrawingTool } from "./toolState";
import "./DrawingLayer.css";

interface DrawingLayerProps {
  chart: IChartApi | null;
  series: ISeriesApi<"Candlestick"> | null;
  symbol: string;
  containerRef: React.RefObject<HTMLDivElement>;
}

const HIT_PAD = 8; // px hit-test tolerance
const DRAG_THRESHOLD = 4; // px moved before a press becomes a drag (vs a click)

/** In-progress creation state. */
interface DraftState {
  tool: string;
  /** Anchors placed so far (data space). */
  points: DrawPoint[];
  /** Live cursor position (data space) for the segment being previewed. */
  hover: DrawPoint | null;
  /** Free-form tools capture points while the pointer is held down. */
  freeform: boolean;
}

/** A press that may become a drag of an existing selection. */
interface PressState {
  id: string;
  anchorIndex: number; // -1 = move whole drawing, >=0 = a specific anchor
  startPx: Px;
  startData: DrawPoint;
  startPoints: DrawPoint[];
  moved: boolean;
}

export default function DrawingLayer({ chart, series, symbol, containerRef }: DrawingLayerProps) {
  const activeTool = useDrawingTool((s) => s.activeTool);
  const setTool = useDrawingTool((s) => s.setActiveTool);
  const clearAllToken = useDrawingTool((s) => s.clearAllToken);
  const store = useDrawings(symbol);
  const { drawings, selectedId, add, updatePoints, update, remove, clearAll, select, selected } =
    store;

  const clipBase = useId().replace(/:/g, "");
  const clipId = `qd-clip-${clipBase}`;

  // "Remove all" is published via toolState.requestClearAll(). React to the
  // incrementing token; ignore the mount value so we don't wipe on load.
  const seenClearToken = useRef(clearAllToken);
  useEffect(() => {
    if (clearAllToken !== seenClearToken.current) {
      seenClearToken.current = clearAllToken;
      clearAll();
    }
  }, [clearAllToken, clearAll]);

  // Re-render on pan/zoom/resize so pixel positions recompute.
  const [, forceTick] = useState(0);
  const bump = () => forceTick((n) => (n + 1) & 0xffff);

  const [size, setSize] = useState({ w: 0, h: 0 });
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const pressRef = useRef<PressState | null>(null);
  const [popoverAt, setPopoverAt] = useState<Px | null>(null);
  const [editingText, setEditingText] = useState<{ id: string; value: string; at: Px } | null>(null);

  // ---- Subscribe to chart range changes + container resize ---------------
  useEffect(() => {
    if (!chart) return;
    const ts = chart.timeScale();
    const onRange = () => bump();
    ts.subscribeVisibleLogicalRangeChange(onRange);
    return () => ts.unsubscribeVisibleLogicalRangeChange(onRange);
  }, [chart]);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      setSize({ w: el.clientWidth, h: el.clientHeight });
      bump();
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [containerRef]);

  // ---- Plot area (excludes the right price axis + bottom time axis) -------
  // Drawings are clipped to this so nothing crosses into the axis gutters.
  const plot = useMemo(() => {
    let priceW = 0;
    let timeH = 0;
    try {
      priceW = chart?.priceScale("right").width() ?? 0;
    } catch {
      priceW = 0;
    }
    try {
      timeH = chart?.timeScale().height() ?? 0;
    } catch {
      timeH = 0;
    }
    return {
      w: Math.max(0, size.w - priceW),
      h: Math.max(0, size.h - timeH),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, size.w, size.h, drawings]);

  const pointerToData = (clientX: number, clientY: number): DrawPoint | null => {
    if (!chart || !series || !svgRef.current) return null;
    const rect = svgRef.current.getBoundingClientRect();
    return toData(chart, series, clientX - rect.left, clientY - rect.top);
  };

  const pointerToPx = (clientX: number, clientY: number): Px => {
    const rect = svgRef.current?.getBoundingClientRect();
    return { x: clientX - (rect?.left ?? 0), y: clientY - (rect?.top ?? 0) };
  };

  // ---- Coordinate converters ---------------------------------------------
  const ctxConv = useMemo(() => {
    if (!chart || !series) return null;
    const toPx = (p: DrawPoint): Px | null => toPixel(chart, series, p);
    const priceToY = (price: number): number | null => series.priceToCoordinate(price);
    const timeToX = (time: number): number | null => {
      const x = chart.timeScale().timeToCoordinate(time as Time);
      return x === null ? null : x;
    };
    return { toPx, priceToY, timeToX };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, series, size.w, size.h, drawings]);

  // ---- Hit testing -------------------------------------------------------
  function hitTest(pt: Px): { drawing: Drawing; anchorIndex: number } | null {
    if (!ctxConv) return null;
    for (let i = drawings.length - 1; i >= 0; i--) {
      const d = drawings[i];
      const anchor = anchorHit(d, pt);
      if (anchor !== null) return { drawing: d, anchorIndex: anchor };
      if (bodyHit(d, pt)) return { drawing: d, anchorIndex: -1 };
    }
    return null;
  }

  function anchorHit(d: Drawing, pt: Px): number | null {
    if (!ctxConv) return null;
    for (let i = 0; i < d.points.length; i++) {
      const px = ctxConv.toPx(d.points[i]);
      if (px && Math.hypot(px.x - pt.x, px.y - pt.y) <= HIT_PAD) return i;
    }
    return null;
  }

  function bodyHit(d: Drawing, pt: Px): boolean {
    if (!ctxConv) return false;
    const P = d.points.map(ctxConv.toPx);
    const a = P[0];
    const b = P[1];
    switch (d.tool) {
      case "trendline":
      case "ray":
      case "extended":
      case "arrow":
      case "fib_fan":
        return !!a && !!b && distToSegment(pt, a, b) <= HIT_PAD;
      case "hline": {
        const y = ctxConv.priceToY(d.points[0].price);
        return y !== null && Math.abs(pt.y - y) <= HIT_PAD;
      }
      case "hray":
        return !!a && Math.abs(pt.y - a.y) <= HIT_PAD;
      case "vline": {
        const x = ctxConv.timeToX(d.points[0].time);
        return x !== null && Math.abs(pt.x - x) <= HIT_PAD;
      }
      case "channel":
      case "fib_retracement":
      case "fib_extension":
      case "rectangle":
      case "ellipse":
      case "triangle":
      case "price_range":
      case "date_range":
      case "long_position":
      case "short_position":
        return !!a && !!b && nearRect(pt, a, b, HIT_PAD);
      case "brush":
      case "highlighter": {
        const pts = P.filter((p): p is Px => p !== null);
        return distToPolyline(pt, pts) <= HIT_PAD + (d.tool === "highlighter" ? d.width * 2 : 0);
      }
      case "text":
        return !!a && nearRect(pt, a, { x: a.x + 60, y: a.y + 12 }, HIT_PAD);
      default:
        return false;
    }
  }

  /** Clamp a popover position so it stays fully on-screen within the chart. */
  function clampPopover(px: Px): Px {
    const W = 220;
    const H = 150;
    return {
      x: Math.max(4, Math.min(px.x, size.w - W - 4)),
      y: Math.max(4, Math.min(px.y, size.h - H - 4)),
    };
  }

  // ---- Pointer handlers --------------------------------------------------
  function onPointerDown(e: React.PointerEvent<SVGSVGElement>) {
    if (!chart || !series) return;
    const px = pointerToPx(e.clientX, e.clientY);
    const data = pointerToData(e.clientX, e.clientY);
    if (!data) return;

    // ----- CURSOR MODE: select / begin move -----
    if (activeTool === "cursor") {
      const hit = hitTest(px);
      if (hit) {
        // Record a press that becomes a drag only after the pointer moves.
        pressRef.current = {
          id: hit.drawing.id,
          anchorIndex: hit.anchorIndex,
          startPx: px,
          startData: data,
          startPoints: hit.drawing.points.map((p) => ({ ...p })),
          moved: false,
        };
        (e.target as Element).setPointerCapture?.(e.pointerId);
      } else {
        // Clicked empty space → clear selection + close popover.
        select(null);
        setPopoverAt(null);
      }
      return;
    }

    // ----- DRAWING MODE -----
    const need = toolPointCount(activeTool);

    if (need === 1) {
      const d = newDrawing(activeTool, [data]);
      add(d);
      if (activeTool === "text") setEditingText({ id: d.id, value: "", at: px });
      setTool("cursor");
      return;
    }

    if (need === -1) {
      // Free-form: capture while held down.
      setDraft({ tool: activeTool, points: [data], hover: data, freeform: true });
      (e.target as Element).setPointerCapture?.(e.pointerId);
      return;
    }

    // Two-point tools: CLICK-move-CLICK.
    if (!draft) {
      // First click → drop the start anchor; preview follows the mouse.
      setDraft({ tool: activeTool, points: [data], hover: data, freeform: false });
    } else {
      // Second click → finalize.
      const finalPts = [draft.points[0], data];
      if (finalPts[0].time !== data.time || finalPts[0].price !== data.price) {
        add(newDrawing(draft.tool, finalPts));
      }
      setDraft(null);
      setTool("cursor");
    }
  }

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const data = pointerToData(e.clientX, e.clientY);
    if (!data) return;
    const px = pointerToPx(e.clientX, e.clientY);

    // Dragging an existing selection (cursor mode).
    const press = pressRef.current;
    if (press) {
      if (!press.moved && Math.hypot(px.x - press.startPx.x, px.y - press.startPx.y) < DRAG_THRESHOLD) {
        return; // still a click, not a drag yet
      }
      press.moved = true;
      const dt = data.time - press.startData.time;
      const dp = data.price - press.startData.price;
      if (press.anchorIndex === -1) {
        updatePoints(
          press.id,
          press.startPoints.map((p) => ({ time: p.time + dt, price: p.price + dp })),
        );
      } else {
        updatePoints(
          press.id,
          press.startPoints.map((p, i) => (i === press.anchorIndex ? { ...data } : p)),
        );
      }
      return;
    }

    // Creation preview.
    if (draft) {
      if (draft.freeform) {
        setDraft({ ...draft, points: [...draft.points, data], hover: data });
      } else {
        setDraft({ ...draft, hover: data });
      }
    }
  }

  function onPointerUp(e: React.PointerEvent<SVGSVGElement>) {
    (e.target as Element).releasePointerCapture?.(e.pointerId);
    const px = pointerToPx(e.clientX, e.clientY);

    // Finish a free-form draw.
    if (draft?.freeform) {
      if (draft.points.length >= 2) add(newDrawing(draft.tool, draft.points));
      setDraft(null);
      setTool("cursor");
      return;
    }

    // Resolve a cursor-mode press: click (select + popover) vs. drag (done).
    const press = pressRef.current;
    if (press) {
      pressRef.current = null;
      if (!press.moved) {
        // Pure click on a drawing → select it and show the popover here.
        select(press.id);
        setPopoverAt(clampPopover(px));
      }
      // If it moved, the drag already updated the points; nothing more to do.
    }
  }

  // ---- Keyboard: delete selection / cancel draft -------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (editingText) return;
      if ((e.key === "Delete" || e.key === "Backspace") && selectedId) {
        e.preventDefault();
        remove(selectedId);
        setPopoverAt(null);
      } else if (e.key === "Escape") {
        setDraft(null);
        setTool("cursor");
        select(null);
        setPopoverAt(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, remove, editingText, setTool, select]);

  // Double-click a text drawing to edit its label.
  function onDoubleClick(e: React.MouseEvent<SVGSVGElement>) {
    const px = pointerToPx(e.clientX, e.clientY);
    const hit = hitTest(px);
    if (hit && hit.drawing.tool === "text") {
      select(hit.drawing.id);
      setEditingText({ id: hit.drawing.id, value: hit.drawing.text ?? "", at: px });
    }
  }

  // ---- Render ------------------------------------------------------------
  const renderCtx = (sel: boolean): RenderCtx | null => {
    if (!ctxConv) return null;
    return { ...ctxConv, width: size.w, height: size.h, selected: sel };
  };

  const cursorMode = activeTool === "cursor";

  return (
    <div className={`qd-drawlayer${cursorMode ? " is-cursor" : " is-drawing"}`}>
      <svg
        ref={svgRef}
        className="qd-drawlayer__svg"
        width={size.w}
        height={size.h}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onDoubleClick={onDoubleClick}
      >
        {/* Clip everything to the price plot area so nothing crosses the axes. */}
        <defs>
          <clipPath id={clipId}>
            <rect x={0} y={0} width={plot.w} height={plot.h} />
          </clipPath>
        </defs>

        <g clipPath={`url(#${clipId})`}>
          {ctxConv &&
            drawings.map((d) => {
              const rc = renderCtx(d.id === selectedId);
              if (!rc) return null;
              return (
                <g key={d.id} className="qd-hit">
                  {renderDrawing(d, rc)}
                </g>
              );
            })}

          {/* Live creation preview */}
          {ctxConv &&
            draft &&
            (() => {
              const rc = renderCtx(false);
              if (!rc) return null;
              const pts = draft.freeform
                ? draft.points
                : [draft.points[0], draft.hover ?? draft.points[0]];
              const preview = newDrawing(draft.tool, pts);
              return <g opacity={0.85}>{renderDrawing(preview, rc)}</g>;
            })()}
        </g>
      </svg>

      {/* Style popover for the selected drawing */}
      {popoverAt && selected && (
        <StylePopover
          at={popoverAt}
          drawing={selected}
          onChange={(patch) => update(selected.id, patch)}
          onDelete={() => {
            remove(selected.id);
            setPopoverAt(null);
          }}
          onClose={() => setPopoverAt(null)}
        />
      )}

      {/* Inline text editor */}
      {editingText && (
        <input
          className="qd-drawlayer__textedit input"
          style={{ left: editingText.at.x, top: editingText.at.y - 10 }}
          autoFocus
          value={editingText.value}
          onChange={(e) => setEditingText({ ...editingText, value: e.target.value })}
          onBlur={() => {
            update(editingText.id, { text: editingText.value });
            setEditingText(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              update(editingText.id, { text: editingText.value });
              setEditingText(null);
            } else if (e.key === "Escape") {
              setEditingText(null);
            }
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function newDrawing(tool: string, points: DrawPoint[]): Drawing {
  return {
    id: makeId(),
    tool,
    points: points.map((p) => ({ ...p })),
    color: DEFAULT_COLOR,
    width: DEFAULT_WIDTH,
    style: DEFAULT_STYLE,
    text: tool === "text" ? "" : undefined,
  };
}

interface StylePopoverProps {
  at: Px;
  drawing: Drawing;
  onChange: (patch: Partial<Drawing>) => void;
  onDelete: () => void;
  onClose: () => void;
}

function StylePopover({ at, drawing, onChange, onDelete, onClose }: StylePopoverProps) {
  const styles: LineStyle[] = ["solid", "dashed", "dotted"];
  return (
    <div
      className="qd-drawpopover card"
      style={{ left: at.x, top: at.y }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="qd-drawpopover__row">
        {PALETTE.map((c) => (
          <button
            key={c}
            type="button"
            className={`qd-drawpopover__swatch${drawing.color === c ? " is-active" : ""}`}
            style={{ background: c }}
            title={c}
            onClick={() => onChange({ color: c })}
          />
        ))}
      </div>
      <div className="qd-drawpopover__row">
        <label className="qd-drawpopover__label">Width</label>
        <input
          type="range"
          min={1}
          max={8}
          value={drawing.width}
          onChange={(e) => onChange({ width: Number(e.target.value) })}
        />
        <span className="mono">{drawing.width}px</span>
      </div>
      <div className="qd-drawpopover__row">
        {styles.map((s) => (
          <button
            key={s}
            type="button"
            className={`btn btn-ghost qd-drawpopover__style${drawing.style === s ? " is-active" : ""}`}
            onClick={() => onChange({ style: s })}
          >
            {s}
          </button>
        ))}
      </div>
      <div className="qd-drawpopover__row qd-drawpopover__actions">
        <button type="button" className="btn btn-sell" onClick={onDelete}>
          Delete
        </button>
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
