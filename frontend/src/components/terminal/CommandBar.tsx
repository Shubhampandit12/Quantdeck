// CommandBar — single natural-language input. Submits to api.aiChat and renders
// the reply plus a compact trace inline. Falls back gracefully if AI is disabled.
import { useState } from "react";
import "./CommandBar.css";
import { api, ApiError } from "../../api/client";
import { useStore } from "../../state/store";
import type { AITraceStep } from "../../api/types";

export default function CommandBar() {
  const activePortfolioId = useStore((s) => s.activePortfolioId);
  const refreshSnapshot = useStore((s) => s.refreshSnapshot);

  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<string | null>(null);
  const [trace, setTrace] = useState<AITraceStep[]>([]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const message = text.trim();
    if (!message || busy) return;
    if (!activePortfolioId) {
      setReply("No active portfolio selected.");
      setTrace([]);
      return;
    }
    setBusy(true);
    setReply(null);
    setTrace([]);
    try {
      const res = await api.aiChat(activePortfolioId, message);
      setReply(res.reply);
      setTrace(res.trace ?? []);
      setText("");
      // The AI may have placed orders — refresh state.
      await refreshSnapshot();
    } catch (err) {
      setReply(err instanceof ApiError ? `Error: ${err.message}` : "AI request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="commandbar">
      {(reply || trace.length > 0) && (
        <div className="cmd-reply">
          {reply && <div className="cmd-reply-text">{reply}</div>}
          {trace.length > 0 && (
            <div className="cmd-trace">
              {trace.map((step, i) => (
                <div className="cmd-trace-step" key={i}>
                  <span className="cmd-trace-kind">{step.kind}</span>
                  {renderStep(step)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <form className="cmd-input-row" onSubmit={submit}>
        <span className="cmd-prompt">›</span>
        <input
          className="input"
          placeholder="Ask the AI or enter an order in plain English (e.g. 'buy 0.5 BTC at market')"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
        />
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "…" : "Send"}
        </button>
        <span className="cmd-hint">Paper money only</span>
      </form>
    </div>
  );
}

function renderStep(step: AITraceStep): string {
  if (step.kind === "tool_call") {
    return `${step.tool ?? "tool"}(${step.args ? JSON.stringify(step.args) : ""})`;
  }
  if (step.kind === "tool_result") {
    return typeof step.result === "string" ? step.result : JSON.stringify(step.result ?? "");
  }
  return step.text ?? "";
}
