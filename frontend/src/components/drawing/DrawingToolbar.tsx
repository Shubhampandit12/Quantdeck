// Vertical left-edge toolbar listing every drawing tool grouped by category.
// Highlights the active tool; a trash button at the bottom clears all drawings.
// Purely presentational: parent (ChartPanel) owns activeTool and passes handlers.
import { CATEGORY_ORDER, CATEGORY_LABELS, TOOLS, type ToolCategory } from "./tools";
import "./DrawingToolbar.css";

interface DrawingToolbarProps {
  activeTool: string;
  onSelect: (tool: string) => void;
  onClearAll: () => void;
}

export default function DrawingToolbar({ activeTool, onSelect, onClearAll }: DrawingToolbarProps) {
  return (
    <div className="qd-drawtoolbar" role="toolbar" aria-label="Drawing tools">
      {CATEGORY_ORDER.map((cat: ToolCategory, ci) => {
        const tools = TOOLS.filter((t) => t.category === cat);
        if (!tools.length) return null;
        return (
          <div className="qd-drawtoolbar__group" key={cat}>
            {ci > 0 && <div className="qd-drawtoolbar__divider" aria-hidden />}
            {tools.map((t) => {
              const active = t.id === activeTool;
              return (
                <button
                  key={t.id}
                  type="button"
                  className={`qd-drawtoolbar__btn${active ? " is-active" : ""}`}
                  aria-pressed={active}
                  title={`${t.label} — ${CATEGORY_LABELS[cat]}`}
                  onClick={() => onSelect(t.id)}
                >
                  <span className="qd-drawtoolbar__icon" aria-hidden>
                    {t.icon}
                  </span>
                  <span className="qd-drawtoolbar__tip" role="tooltip">
                    {t.label}
                  </span>
                </button>
              );
            })}
          </div>
        );
      })}

      <div className="qd-drawtoolbar__spacer" />
      <div className="qd-drawtoolbar__divider" aria-hidden />
      <button
        type="button"
        className="qd-drawtoolbar__btn qd-drawtoolbar__trash"
        title="Remove all drawings"
        onClick={onClearAll}
      >
        <span className="qd-drawtoolbar__icon" aria-hidden>
          🗑
        </span>
        <span className="qd-drawtoolbar__tip" role="tooltip">
          Remove all
        </span>
      </button>
    </div>
  );
}
