// Absolutely-positioned SVG overlay aligned to the lightweight-charts pane.
//
// Responsibilities:
//   - Convert drawings between data space and pixels every render, re-rendering
//     on pan/zoom (subscribeVisibleLogicalRangeChange) and on resize
//     (ResizeObserver on containerRef).
//   - When the active tool != 'cursor': click-drag creates a drawing.
//   - When 'cursor': hit-test to select a drawing, drag its anchors, and press
//     Delete/Backspace to remove the selected one.
//   - A small popover lets the user edit color / width / style of the selection.
//
// The active tool is read from the shared `useDrawingTool` store (set by
// DrawingToolbar via ChartPanel), keeping this component's frozen prop
// signature { chart, series, symbol, containerRef } unchanged.
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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

/** Live in-progress creation state. */
interface DraftState {
  tool: string;
  points: DrawPoint[];
  /** For free-form tools, whether the pointer is currently down. */
  active: boolean;
}

/** Which anchor of the selected drawing is being dragged (or -2 = whole move). */
interface DragState {
  id: string;
  anchorIndex: number; // -1 = move all, >=0 = specific anchor
  startPointer: DrawPoint;
  startPoints: DrawPoint[];
}

export default function DrawingLayer({ chart, series, symbol, containerRef }: DrawingLayerProps) {
  const activeTool = useDrawingTool((s) => s.activeTool);
  const setTool = useDrawingTool((s) => s.setActiveTool);
  const clearAllToken = useDrawingTool((s) => s.clearAllToken);
  const store = useDrawings(symbol);
  const { drawings, selectedId, add, updatePoints, update, remove, clearAll, select, selected } =
    store;

  // "Remove all" is published via toolState.requestClearAll() (e.g. from the
  // DrawingToolbar trash button). We react to the incrementing token. The very
  // first render's token is ignored so we don't wipe on mount.
  const seenClearToken = useRef(clearAllToken);
  useEffect(() => {
    if (clearAllToken !== seenClearToken.current) {
      seenClearToken.current = clearAllToken;
      clearAll();
    }
  }, [clearAllToken, clearAll]);

  // Force re-render whenever the chart pans/zooms/resizes so pixel positions
  // recompute. We bump a counter rather than storing pixels in state.
  const [, forceTick] = useState(0);
  const bump = () => forceTick((n) => (n + 1) & 0xffff);

  const [size, setSize] = useState({ w: 0, h: 0 });
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const [popoverAt, setPopoverAt] = useState<Px | null>(null);
  const [editingText, setEditingText] = useState<{ id: string; value: string; at: Px } | null>(null);

  // ---- Subscribe to chart range changes + container resize ---------------
  useEffect(() => {
    if (!chart) return;
    const ts = chart.timeScale();
    const onRange = () => bump();
    ts.subscribeVisibleLogicalRangeChange(onRange);
    return () => {
      ts.unsubscribeVisibleLogicalRangeChange(onRange);
    };
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

  // ---- Coordinate converters (memoized on tick/size) ---------------------
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

  const pointerToData = (clientX: number, clientY: number): DrawPoint | null => {
    if (!chart || !series || !svgRef.current) return null;
    const rect = svgRef.current.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    return toData(chart, series, x, y);
  };

  const pointerToPx = (clientX: number, clientY: number): Px => {
    const rect = svgRef.current?.getBoundingClientRect();
    return { x: clientX - (rect?.left ?? 0), y: clientY - (rect?.top ?? 0) };
  };

  // ---- Hit testing -------------------------------------------------------
  function hitTest(pt: Px): { drawing: Drawing; anchorIndex: number } | null {
    if (!ctxConv) return null;
    // Iterate topmost-first (last added renders on top).
    for (let i = drawings.length - 1; i >= 0; i--) {
      const d = drawings[i];
      const anchor = anchorHit(d, pt);
      if (anchor !== null) return { drawing: d, anchorIndex: anchor };
      if (bodyHit(d, pt)) return { drawing: d, anchorIndex: -1 };
    }
    return null;
  }

  /** Returns the anchor index near `pt`, or null. */
  function anchorHit(d: Drawing, pt: Px): number | null {
    if (!ctxConv) return null;
    for (let i = 0; i < d.points.length; i++) {
      const px = ctxConv.toPx(d.points[i]);
      if (px && Math.hypot(px.x - pt.x, px.y - pt.y) <= HIT_PAD) return i;
    }
    return null;
  }

  /** Body hit-test per tool. */
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

  // ---- Pointer handlers --------------------------------------------------
  function onPointerDown(e: React.PointerEvent<SVGSVGElement>) {
    if (!chart || !series) return;
    const px = pointerToPx(e.clientX, e.clientY);
    const data = pointerToData(e.clientX, e.clientY);
    if (!data) return;

    if (activeTool === "cursor") {
      // Select / begin drag.
      const hit = hitTest(px);
      if (hit) {
        select(hit.drawing.id);
        setPopoverAt(null);
        dragRef.current = {
          id: hit.drawing.id,
          anchorIndex: hit.anchorIndex,
          startPointer: data,
          startPoints: hit.drawing.points.map((p) => ({ ...p })),
        };
        (e.target as Element).setPointerCapture?.(e.pointerId);
      } else {
        select(null);
        setPopoverAt(null);
      }
      return;
    }

    // Creation mode.
    const need = toolPointCount(activeTool);
    if (need === 1) {
      // Single-click tools finalize immediately (text opens an editor).
      const d = newDrawing(activeTool, [data]);
      add(d);
      if (activeTool === "text") {
        setEditingText({ id: d.id, value: "", at: px });
      }
      setTool("cursor");
      return;
    }
    if (need === -1) {
      // Free-form: start collecting points.
      setDraft({ tool: activeTool, points: [data], active: true });
      (e.target as Element).setPointerCapture?.(e.pointerId);
      return;
    }
    // Two-point: start a drag-draft.
    setDraft({ tool: activeTool, points: [data, data], active: true });
    (e.target as Element).setPointerCapture?.(e.pointerId);
  }

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const data = pointerToData(e.clientX, e.clientY);
    if (!data) return;

    // Dragging an existing selection.
    const drag = dragRef.current;
    if (drag) {
      const dt = data.time - drag.startPointer.time;
      const dp = data.price - drag.startPointer.price;
      if (drag.anchorIndex === -1) {
        const moved = drag.startPoints.map((p) => ({ time: p.time + dt, price: p.price + dp }));
        updatePoints(drag.id, moved);
      } else {
        const moved = drag.startPoints.map((p, i) => (i === drag.anchorIndex ? { ...data } : p));
        updatePoints(drag.id, moved);
      }
      return;
    }

    // Extending a draft.
    if (draft?.active) {
      if (toolPointCount(draft.tool) === -1) {
        setDraft({ ...draft, points: [...draft.points, data] });
      } else {
        setDraft({ ...draft, points: [draft.points[0], data] });
      }
    }
  }

  function onPointerUp(e: React.PointerEvent<SVGSVGElement>) {
    (e.target as Element).releasePointerCapture?.(e.pointerId);

    if (dragRef.current) {
      dragRef.current = null;
      return;
    }

    if (draft) {
      // Finalize the draft into a real drawing (ignore zero-size accidents).
      const pts = draft.points;
      const enough =
        toolPointCount(draft.tool) === -1
          ? pts.length >= 2
          : pts.length >= 2 && (pts[0].time !== pts[1].time || pts[0].price !== pts[1].price);
      if (enough) {
        add(newDrawing(draft.tool, pts));
      }
      setDraft(null);
      setTool("cursor");
    }
  }

  // ---- Keyboard: delete selected -----------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (editingText) return; // don't hijack text editing
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

  // Show the style popover when a drawing is selected (double-click / on select).
  function onDoubleClick(e: React.MouseEvent<SVGSVGElement>) {
    const px = pointerToPx(e.clientX, e.clientY);
    const hit = hitTest(px);
    if (hit) {
      select(hit.drawing.id);
      if (hit.drawing.tool === "text") {
        setEditingText({ id: hit.drawing.id, value: hit.drawing.text ?? "", at: px });
      } else {
        setPopoverAt(px);
      }
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
        {/* Persisted drawings. Each is wrapped in `.qd-hit` so that, in cursor
            mode (where the SVG itself is pointer-events:none to let the chart
            zoom/pan), the painted shape remains a pointer target and stays
            selectable/draggable. pointer-events is an inherited property, so the
            wrapper's value propagates to the shape's lines/rects. */}
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

        {/* Live draft preview */}
        {ctxConv &&
          draft &&
          (() => {
            const rc = renderCtx(false);
            if (!rc) return null;
            const preview = newDrawing(draft.tool, draft.points);
            return <g opacity={0.85}>{renderDrawing(preview, rc)}</g>;
          })()}
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
    <div className="qd-drawpopover card" style={{ left: at.x + 8, top: at.y + 8 }} onPointerDown={(e) => e.stopPropagation()}>
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
