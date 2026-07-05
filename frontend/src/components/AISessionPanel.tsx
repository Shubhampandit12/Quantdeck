// AISessionPanel — drive an autonomous AI paper-trading session.
//
// Layout:
//  1. Header: AI enabled/disabled status + model, and the ALWAYS-VISIBLE red
//     "Flatten all / Kill switch" button (api.flatten).
//  2. Strategy prompt textarea + guardrails form + Start/Pause/Resume/Stop.
//  3. Live color-coded trace transcript (openEventSocket ai_trace + poll fallback).
//  4. A simpler one-off chat box (api.aiChat) for manual NL orders.
//
// Everything is demo money only. All colors come from theme tokens.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AITraceStep } from "../api/types";
import { api, ApiError } from "../api/client";
import { openEventSocket } from "../api/ws";
import "./AISessionPanel.css";

interface Props {
  portfolioId: string;
}

// Session lifecycle status returned by the backend (string; normalize loosely).
type SessionStatus = "idle" | "running" | "paused" | "stopped" | "error" | string;

// Guardrails form state — all strings so the inputs stay controlled; parsed on submit.
interface GuardrailForm {
  allowedSymbols: string;
  maxOrderNotional: string;
  maxOrdersPerMin: string;
  maxDailyLoss: string;
}

const EMPTY_GUARDRAILS: GuardrailForm = {
  allowedSymbols: "",
  maxOrderNotional: "",
  maxOrdersPerMin: "",
  maxDailyLoss: "",
};

// One entry in the rendered transcript: either a session trace step or a
// one-off chat exchange. We keep them in one list with an id so React keys stay
// stable and we can auto-scroll.
interface TraceEntry {
  id: string;
  step: AITraceStep;
}

let traceSeq = 0;
function nextTraceId(): string {
  traceSeq += 1;
  return `t${traceSeq}`;
}

/** Parse a numeric text field → number | undefined (blank/invalid = undefined). */
function parseNum(v: string): number | undefined {
  const t = v.trim();
  if (!t) return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

/** Parse a comma list of symbols → string[] | undefined. */
function parseSymbols(v: string): string[] | undefined {
  const list = v
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  return list.length ? list : undefined;
}

/** Short human timestamp (HH:MM:SS) from an ISO string. */
function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-US", { hour12: false });
}

/** Pretty-print an unknown value for tool args/results without throwing. */
function stringify(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

export default function AISessionPanel({ portfolioId }: Props) {
  // AI availability (from api.aiStatus).
  const [aiEnabled, setAiEnabled] = useState<boolean | null>(null);
  const [aiModel, setAiModel] = useState<string>("");

  // Session state.
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [prompt, setPrompt] = useState("");
  const [guardrails, setGuardrails] = useState<GuardrailForm>(EMPTY_GUARDRAILS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Unified trace transcript.
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const traceEndRef = useRef<HTMLDivElement | null>(null);

  // One-off chat.
  const [chatMsg, setChatMsg] = useState("");
  const [chatBusy, setChatBusy] = useState(false);

  // Keep the latest sessionId for the interval/socket callbacks without
  // re-subscribing every render.
  const sessionIdRef = useRef<string | null>(null);
  sessionIdRef.current = sessionId;

  const disabled = aiEnabled === false;

  // -- AI status on mount ------------------------------------------------
  useEffect(() => {
    let alive = true;
    api
      .aiStatus()
      .then((s) => {
        if (!alive) return;
        setAiEnabled(s.enabled);
        setAiModel(s.model);
      })
      .catch(() => {
        if (!alive) return;
        setAiEnabled(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // -- Append trace steps, de-duping and auto-scrolling ------------------
  const appendSteps = useCallback((steps: AITraceStep[]) => {
    if (!steps.length) return;
    setTrace((prev) => {
      const entries = steps.map((step) => ({ id: nextTraceId(), step }));
      // Cap history so long sessions don't grow unbounded.
      const merged = [...prev, ...entries];
      return merged.length > 500 ? merged.slice(merged.length - 500) : merged;
    });
  }, []);

  // Replace the whole trace from a polled session snapshot (authoritative).
  const replaceTrace = useCallback((steps: AITraceStep[]) => {
    setTrace(steps.map((step) => ({ id: nextTraceId(), step })));
  }, []);

  // Auto-scroll to the newest trace entry.
  useEffect(() => {
    traceEndRef.current?.scrollIntoView({ block: "end" });
  }, [trace]);

  // -- Live event socket: append ai_trace events -------------------------
  useEffect(() => {
    if (!portfolioId) return;
    const sock = openEventSocket(portfolioId, (e) => {
      if (e.type === "ai_trace") {
        // The backend may send a single step under `data` (matching AITraceStep).
        const d = e.data as unknown as Partial<AITraceStep> | undefined;
        if (d && typeof d.kind === "string") {
          appendSteps([
            {
              kind: d.kind,
              text: d.text,
              tool: d.tool,
              args: d.args,
              result: d.result,
              timestamp: d.timestamp ?? e.timestamp,
            },
          ]);
        }
      } else if (e.type === "ai_status") {
        const d = e.data as unknown as { status?: string; session_id?: string } | undefined;
        if (d?.status) setStatus(d.status);
        if (d?.session_id) setSessionId(d.session_id);
      }
    });
    return () => sock.close();
  }, [portfolioId, appendSteps]);

  // -- Poll fallback: refresh status + trace every ~2s while a session exists.
  useEffect(() => {
    if (!sessionId) return;
    let alive = true;
    const tick = async () => {
      const sid = sessionIdRef.current;
      if (!sid) return;
      try {
        const s = await api.aiSession(sid);
        if (!alive) return;
        setStatus(s.status);
        // Authoritative refresh from the poll keeps us in sync even if a
        // socket frame was dropped.
        replaceTrace(s.trace);
      } catch {
        /* transient — keep last known state */
      }
    };
    void tick();
    const iv = window.setInterval(tick, 2000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, [sessionId, replaceTrace]);

  // -- Derived control enablement ---------------------------------------
  const hasSession = sessionId !== null;
  const isRunning = status === "running";
  const isPaused = status === "paused";
  const isTerminal = status === "stopped" || status === "error";

  const canStart = !disabled && !busy && (!hasSession || isTerminal) && prompt.trim().length > 0;
  const canPause = !disabled && !busy && hasSession && isRunning;
  const canResume = !disabled && !busy && hasSession && isPaused;
  const canStop = !disabled && !busy && hasSession && !isTerminal;

  // -- Actions -----------------------------------------------------------
  const withBusy = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  const guardrailsPayload = useMemo(() => {
    const g: Record<string, unknown> = {};
    const syms = parseSymbols(guardrails.allowedSymbols);
    if (syms) g.allowed_symbols = syms;
    const mon = parseNum(guardrails.maxOrderNotional);
    if (mon !== undefined) g.max_order_notional = mon;
    const opm = parseNum(guardrails.maxOrdersPerMin);
    if (opm !== undefined) g.max_orders_per_min = opm;
    const dl = parseNum(guardrails.maxDailyLoss);
    if (dl !== undefined) g.max_daily_loss = dl;
    return g;
  }, [guardrails]);

  const onStart = () =>
    withBusy(async () => {
      // Fresh session — clear old transcript.
      setTrace([]);
      const res = await api.aiSessionStart(
        portfolioId,
        prompt.trim(),
        Object.keys(guardrailsPayload).length ? guardrailsPayload : undefined,
      );
      setSessionId(res.session_id);
      setStatus(res.status);
    });

  const onPause = () =>
    withBusy(async () => {
      if (!sessionId) return;
      const res = await api.aiSessionPause(sessionId);
      setStatus(res.status);
    });

  const onResume = () =>
    withBusy(async () => {
      if (!sessionId) return;
      const res = await api.aiSessionResume(sessionId);
      setStatus(res.status);
    });

  const onStop = () =>
    withBusy(async () => {
      if (!sessionId) return;
      const res = await api.aiSessionStop(sessionId);
      setStatus(res.status);
    });

  // Kill switch — flatten every position/order immediately. Always available.
  const onFlatten = () =>
    withBusy(async () => {
      const orders = await api.flatten(portfolioId);
      appendSteps([
        {
          kind: "message",
          text: `Kill switch: flatten requested — ${orders.length} order(s) submitted to close all positions.`,
          timestamp: new Date().toISOString(),
        },
      ]);
    });

  const onChatSend = () => {
    const msg = chatMsg.trim();
    if (!msg || disabled || chatBusy) return;
    setChatBusy(true);
    setError(null);
    // Echo the user's message into the transcript.
    appendSteps([{ kind: "message", text: `You: ${msg}`, timestamp: new Date().toISOString() }]);
    api
      .aiChat(portfolioId, msg)
      .then((res) => {
        appendSteps(res.trace);
        appendSteps([
          { kind: "message", text: `AI: ${res.reply}`, timestamp: new Date().toISOString() },
        ]);
        setChatMsg("");
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setChatBusy(false));
  };

  // Cmd/Ctrl+Enter sends the chat message.
  const onChatKeyDown = (ev: React.KeyboardEvent<HTMLInputElement>) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      onChatSend();
    }
  };

  const setGuard = (patch: Partial<GuardrailForm>) =>
    setGuardrails((g) => ({ ...g, ...patch }));

  return (
    <div className="ai-panel">
      {/* Header: status + kill switch --------------------------------- */}
      <div className="ai-header">
        <div className="ai-header-left">
          <span className="ai-title">AI SESSION</span>
          {aiEnabled === null ? (
            <span className="ai-status-chip ai-status-loading">checking…</span>
          ) : aiEnabled ? (
            <span className="ai-status-chip ai-status-on">
              enabled{aiModel ? ` · ${aiModel}` : ""}
            </span>
          ) : (
            <span className="ai-status-chip ai-status-off">disabled</span>
          )}
          {hasSession && (
            <span className={`ai-status-chip ai-session-${status}`}>{status}</span>
          )}
        </div>
        {/* Always-visible kill switch. */}
        <button
          type="button"
          className="ai-kill-btn"
          onClick={onFlatten}
          disabled={busy}
          title="Flatten all positions & cancel open orders now"
        >
          ⏻ Flatten all / Kill switch
        </button>
      </div>

      {disabled && (
        <div className="ai-disabled-note">
          AI disabled — set <code>ANTHROPIC_API_KEY</code> on the backend to enable
          autonomous trading. Controls below are shown but inactive.
        </div>
      )}

      {error && <div className="ai-error">{error}</div>}

      {/* Body: two columns — controls + transcript --------------------- */}
      <div className="ai-body">
        <div className="ai-controls">
          <label className="ai-field">
            <span className="ai-label">Strategy prompt</span>
            <textarea
              className="input ai-prompt"
              placeholder="Describe the strategy in plain English, e.g. 'Buy dips in BTC and ETH, take profit at +3%, keep total exposure under $20k…'"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={disabled || (hasSession && !isTerminal)}
              rows={5}
            />
          </label>

          <div className="ai-guardrails">
            <div className="ai-label">Guardrails</div>
            <label className="ai-field">
              <span className="ai-sublabel">Allowed symbols (comma list)</span>
              <input
                className="input"
                placeholder="BTC/USDT, ETH/USDT, AAPL"
                value={guardrails.allowedSymbols}
                onChange={(e) => setGuard({ allowedSymbols: e.target.value })}
                disabled={disabled}
              />
            </label>
            <div className="ai-guardrail-row">
              <label className="ai-field">
                <span className="ai-sublabel">Max order notional ($)</span>
                <input
                  className="input"
                  type="number"
                  min={0}
                  placeholder="5000"
                  value={guardrails.maxOrderNotional}
                  onChange={(e) => setGuard({ maxOrderNotional: e.target.value })}
                  disabled={disabled}
                />
              </label>
              <label className="ai-field">
                <span className="ai-sublabel">Max orders / min</span>
                <input
                  className="input"
                  type="number"
                  min={0}
                  placeholder="10"
                  value={guardrails.maxOrdersPerMin}
                  onChange={(e) => setGuard({ maxOrdersPerMin: e.target.value })}
                  disabled={disabled}
                />
              </label>
            </div>
            <label className="ai-field">
              <span className="ai-sublabel">Max daily loss ($)</span>
              <input
                className="input"
                type="number"
                min={0}
                placeholder="2000"
                value={guardrails.maxDailyLoss}
                onChange={(e) => setGuard({ maxDailyLoss: e.target.value })}
                disabled={disabled}
              />
            </label>
          </div>

          {/* Lifecycle buttons. */}
          <div className="ai-actions">
            <button
              type="button"
              className="btn btn-primary ai-start"
              onClick={onStart}
              disabled={!canStart}
            >
              {isTerminal && hasSession ? "Restart" : "Start"}
            </button>
            <button type="button" className="btn" onClick={onPause} disabled={!canPause}>
              Pause
            </button>
            <button type="button" className="btn" onClick={onResume} disabled={!canResume}>
              Resume
            </button>
            <button type="button" className="btn ai-stop" onClick={onStop} disabled={!canStop}>
              Stop
            </button>
          </div>

          {/* One-off NL chat / order box. */}
          <div className="ai-chat">
            <div className="ai-label">Quick chat / manual order</div>
            <div className="ai-chat-row">
              <input
                className="input"
                placeholder='e.g. "buy 10 AAPL" or "what is my exposure?"'
                value={chatMsg}
                onChange={(e) => setChatMsg(e.target.value)}
                onKeyDown={onChatKeyDown}
                disabled={disabled || chatBusy}
              />
              <button
                type="button"
                className="btn btn-primary"
                onClick={onChatSend}
                disabled={disabled || chatBusy || !chatMsg.trim()}
              >
                {chatBusy ? "…" : "Send"}
              </button>
            </div>
          </div>
        </div>

        {/* Live trace transcript ------------------------------------- */}
        <div className="ai-trace">
          <div className="panel-title ai-trace-title">Live trace</div>
          <div className="ai-trace-scroll">
            {trace.length === 0 ? (
              <div className="ai-trace-empty muted">
                No activity yet. Start a session or send a quick chat message.
              </div>
            ) : (
              trace.map(({ id, step }) => <TraceRow key={id} step={step} />)
            )}
            <div ref={traceEndRef} />
          </div>
        </div>
      </div>
    </div>
  );
}

// A single color-coded trace row. Distinct styling per kind.
function TraceRow({ step }: { step: AITraceStep }) {
  const ts = fmtTime(step.timestamp);
  const label =
    step.kind === "thought"
      ? "thought"
      : step.kind === "tool_call"
        ? "tool call"
        : step.kind === "tool_result"
          ? "result"
          : "message";

  return (
    <div className={`ai-trace-row ai-trace-${step.kind}`}>
      <div className="ai-trace-meta">
        <span className={`ai-trace-kind ai-kind-${step.kind}`}>{label}</span>
        {ts && <span className="ai-trace-ts mono">{ts}</span>}
      </div>
      <div className="ai-trace-content">
        {step.kind === "tool_call" ? (
          <>
            <span className="ai-trace-tool mono">{step.tool ?? "tool"}</span>
            {step.args !== undefined && (
              <pre className="ai-trace-code mono">{stringify(step.args)}</pre>
            )}
          </>
        ) : step.kind === "tool_result" ? (
          <>
            {step.tool && <span className="ai-trace-tool mono">{step.tool}</span>}
            <pre className="ai-trace-code mono">{stringify(step.result)}</pre>
          </>
        ) : (
          <span className="ai-trace-text">{step.text ?? stringify(step.result)}</span>
        )}
      </div>
    </div>
  );
}
