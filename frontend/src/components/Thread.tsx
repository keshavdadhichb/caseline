/* Centre thread — user turns, agent prose, the live execution plan with
   Chose/Because/Returned detail, result chips, clarification, empty and
   error states. Mirrors the design's inThread branch. */

import { useState } from "react";
import { Chevron, Collapse, DetailRow, SendIcon } from "./ui";
import { typologyLabel, type NarratedStep } from "../api";
import type { ChipRef, Message } from "../App";

function StepRow({ step }: { step: NarratedStep }) {
  const [open, setOpen] = useState(false);
  const dim = step.skipped ? 0.55 : 1;

  return (
    <div style={{ borderTop: "1px solid var(--border)", opacity: dim, animation: "fadeUp8 300ms var(--ease) both" }}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="hv-row"
        style={{ display: "flex", alignItems: "center", gap: 12, width: "100%", minHeight: 40, padding: "8px 16px" }}>
        <span style={{ width: 16, height: 16, flex: "none", display: "flex", alignItems: "center", justifyContent: "center" }}>
          {step.state === "running" && <span className="dot" style={{ background: "var(--accent)", animation: "pulseDot 1.4s ease-in-out infinite" }} />}
          {step.state === "done" && (
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
              <path d="M2.5 6.5 5 9l4.5-6" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
                pathLength={12} style={{ strokeDasharray: 12, animation: "tickDraw 200ms ease-out both" }} />
            </svg>
          )}
          {step.state === "error" && <span className="dot" style={{ background: "var(--risk-high-dot)" }} />}
          {step.skipped && <span style={{ width: 10, height: 10, borderRadius: 999, border: "1.5px dashed var(--faint)" }} />}
          {step.state === "pending" && <span className="dot" style={{ background: "var(--border-strong)" }} />}
        </span>

        <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, lineHeight: 1.5, display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <span style={{ position: "relative" }}>
            {step.name}
            {step.skipped && (
              <span style={{ position: "absolute", left: 0, right: 0, top: "50%", height: 1, background: "var(--faint)", transformOrigin: "left", animation: "strike 240ms ease-out both" }} />
            )}
          </span>
          {step.skipped && step.skip_reason && (
            <span style={{ color: "var(--faint)", fontSize: 13.5 }}>· {step.skip_reason}</span>
          )}
        </span>

        {step.output && (
          <span className="mono" style={{ fontSize: 13, lineHeight: 1.4, color: "var(--faint)", flex: "none" }}>{step.output}</span>
        )}
        <Chevron open={open} />
      </button>

      <Collapse open={open}>
        <div style={{ padding: "2px 16px 14px 44px", display: "flex", flexDirection: "column", gap: 8 }}>
          <DetailRow k="Chose" v={step.chose} keyWidth={76} />
          <DetailRow k="Because" v={step.because || "—"} keyWidth={76} />
          <DetailRow k="Returned" v={step.returned} keyWidth={76} />
        </div>
      </Collapse>
    </div>
  );
}

function AgentTurn({ m, onOpenChip, onRetry, onAnswer }: {
  m: Message;
  onOpenChip: (c: ChipRef) => void;
  onRetry: () => void;
  onAnswer: (a: string) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 14, animation: "fadeUp 300ms var(--ease) both" }}>
      <div style={{ width: 26, height: 26, flex: "none", borderRadius: 8, background: "var(--accent-tint)", display: "flex", alignItems: "center", justifyContent: "center", marginTop: 1 }}>
        <span className="dot" style={{ background: "var(--accent)" }} />
      </div>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
        {m.thinking && (
          <div style={{ display: "flex", gap: 5, height: 26, alignItems: "center" }} role="status" aria-label="Working">
            {[0, 0.15, 0.3].map((d) => (
              <span key={d} className="dot" style={{ background: "var(--accent)", animation: "dotsPulse 1.2s infinite", animationDelay: `${d}s` }} />
            ))}
          </div>
        )}

        {m.prose1 && <p style={{ margin: 0, fontSize: 15, lineHeight: 1.62, maxWidth: "62ch", animation: "fadeUp 300ms var(--ease) both" }}>{m.prose1}</p>}

        {m.steps && m.steps.length > 0 && (
          <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 14, overflow: "hidden", animation: "fadeUp 300ms var(--ease) both" }}>
            <div className="label" style={{ padding: "14px 16px 10px", marginBottom: 0 }}>Execution plan</div>
            {m.steps.map((s) => <StepRow key={s.tool} step={s} />)}
          </div>
        )}

        {m.prose2 && <p style={{ margin: 0, fontSize: 15, lineHeight: 1.62, maxWidth: "62ch", animation: "fadeUp 300ms var(--ease) both" }}>{m.prose2}</p>}

        {m.chips && m.chips.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, animation: "fadeUp 300ms var(--ease) both", animationDelay: "120ms" }}>
            {m.chips.map((c) => (
              <button key={c.kind + c.label} onClick={() => onOpenChip(c)}
                style={{ border: `1px solid ${c.accent ? "var(--accent-tint-border)" : "var(--border)"}`, background: c.accent ? "var(--accent-tint)" : "var(--surface)", borderRadius: 14, padding: "12px 14px", display: "flex", flexDirection: "column", gap: 2, transition: "border-color 120ms, background 120ms" }}>
                <span style={{ fontSize: 16, fontWeight: 500, letterSpacing: "-0.01em", lineHeight: 1.45 }}>{c.label}</span>
                <span className="mono" style={{ fontSize: 13, lineHeight: 1.4, color: "var(--muted)" }}>{c.detail}</span>
              </button>
            ))}
          </div>
        )}

        {m.clarify && (
          <div style={{ display: "flex", flexDirection: "column", gap: 14, animation: "fadeUp 300ms var(--ease) both" }}>
            <p style={{ margin: 0, fontSize: 15, lineHeight: 1.62 }}>I need one detail before I run this.</p>
            <div style={{ fontSize: 16, fontWeight: 500, letterSpacing: "-0.01em", lineHeight: 1.45 }}>{m.clarify}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {["The last 30 days", "The last 90 days", "The full dataset window"].map((o) => (
                <button key={o} onClick={() => onAnswer(o)} className="hv-violet"
                  style={{ display: "flex", alignItems: "center", width: "100%", height: 44, padding: "0 14px", borderRadius: 10, fontSize: 13.5, color: "var(--muted)", border: "1px solid var(--border)" }}>
                  {o}
                </button>
              ))}
            </div>
          </div>
        )}

        {m.typologies && m.typologies.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 14, animation: "fadeUp 300ms var(--ease) both" }}>
            {m.typologies.map((t) => (
              <div key={t.name} style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 14, padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
                <span style={{ fontSize: 16, fontWeight: 500, letterSpacing: "-0.01em", lineHeight: 1.45 }}>{typologyLabel(t.name)}</span>
                <p style={{ margin: 0, fontSize: 14, lineHeight: 1.6, maxWidth: "62ch" }}>{t.what}</p>
                <div style={{ display: "flex", gap: 12, fontSize: 13.5, lineHeight: 1.5 }}>
                  <span className="label" style={{ width: 76, flex: "none", paddingTop: 3, marginBottom: 0 }}>Rule</span>
                  <span className="mono" style={{ fontSize: 13, color: "var(--ink)", maxWidth: "56ch" }}>{t.rule}</span>
                </div>
                <div style={{ display: "flex", gap: 12, fontSize: 13.5, lineHeight: 1.5 }}>
                  <span className="label" style={{ width: 76, flex: "none", paddingTop: 3, marginBottom: 0 }}>Why</span>
                  <span style={{ color: "var(--muted)", maxWidth: "56ch" }}>{t.why}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {m.empty && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "16px 0", animation: "fadeUp 300ms var(--ease) both" }}>
            <div style={{ fontSize: 16, fontWeight: 500, letterSpacing: "-0.01em", lineHeight: 1.45 }}>No accounts met the detection thresholds for this question.</div>
            <p style={{ margin: 0, fontSize: 15, lineHeight: 1.62, color: "var(--muted)", maxWidth: "62ch" }}>
              Widening the window or asking about a different typology may surface more.
            </p>
          </div>
        )}

        {m.error && (
          <div style={{ display: "flex", flexDirection: "column", gap: 14, animation: "fadeUp 300ms var(--ease) both" }}>
            <p style={{ margin: 0, fontSize: 15, lineHeight: 1.62, maxWidth: "62ch" }}>{m.error}</p>
            <button onClick={onRetry} className="hv-tint"
              style={{ height: 38, padding: "0 16px", borderRadius: 10, border: "1px solid var(--border)", fontSize: 13.5, fontWeight: 500, color: "var(--ink)", alignSelf: "flex-start" }}>
              Try again
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function Thread({
  messages, draft, onDraft, onSend, onOpenChip, onRetry, onAnswer, bottomRef,
}: {
  messages: Message[];
  draft: string;
  onDraft: (v: string) => void;
  onSend: () => void;
  onOpenChip: (c: ChipRef) => void;
  onRetry: () => void;
  onAnswer: (a: string) => void;
  bottomRef: React.RefObject<HTMLDivElement | null>;
}) {
  const [focused, setFocused] = useState(false);

  return (
    <>
      <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
        <div style={{ maxWidth: 680, margin: "0 auto", padding: "40px 24px 32px", display: "flex", flexDirection: "column", gap: 32 }}>
          {messages.map((m) => (
            <div key={m.id}>
              {m.role === "user" ? (
                <div style={{ display: "flex", justifyContent: "flex-end", animation: "fadeUp 300ms var(--ease) both" }}>
                  <div style={{ background: "var(--tint)", borderRadius: 14, padding: "10px 16px", fontSize: 15, lineHeight: 1.62, maxWidth: "80%" }}>{m.text}</div>
                </div>
              ) : (
                <AgentTurn m={m} onOpenChip={onOpenChip} onRetry={onRetry} onAnswer={onAnswer} />
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>

      <div style={{ borderTop: "1px solid var(--border)", background: "var(--bg)" }}>
        <div style={{ maxWidth: 680, margin: "0 auto", padding: "12px 24px 16px" }}>
          <div style={{ height: 52, background: "var(--surface)", border: `1px solid ${focused ? "var(--accent-tint-border)" : "var(--border)"}`, borderRadius: 14, display: "flex", alignItems: "center", gap: 12, padding: "0 9px 0 16px", transition: "border-color 120ms" }}>
            <input
              aria-label="Ask a follow-up"
              value={draft}
              onChange={(e) => onDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && draft.trim()) onSend(); }}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder="Ask a follow-up; Caseline plans the checks"
              style={{ flex: 1, minWidth: 0, border: "none", background: "transparent", fontSize: 15, lineHeight: 1.62, padding: 0, outline: "none" }}
            />
            <button aria-label="Send" onClick={onSend} className="hv-accent"
              style={{ width: 34, height: 34, flex: "none", borderRadius: 999, background: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <SendIcon />
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
