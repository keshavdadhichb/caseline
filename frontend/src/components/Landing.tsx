/* Landing screen — animated wordmark, hero, composer and the three
   problem-statement query chips, per the design's isLanding branch. */

import { useState } from "react";
import { SendIcon } from "./ui";
import { num } from "../api";

const WORDMARK = "Caseline";

export function Landing({
  draft, onDraft, onSend, suggestions, onPick, onAbout, statsLine,
}: {
  draft: string;
  onDraft: (v: string) => void;
  onSend: () => void;
  suggestions: string[];
  onPick: (q: string) => void;
  onAbout: () => void;
  statsLine: { n_txns: number } | null;
}) {
  const [focused, setFocused] = useState(false);

  return (
    <div className="scroll" style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ width: 620, maxWidth: "calc(100% - 48px)", padding: "40px 0" }}>
        <div style={{ textAlign: "center", marginBottom: 40, fontSize: 19, fontWeight: 500, letterSpacing: "-0.015em", color: "var(--ink)" }}>
          {WORDMARK.split("").map((ch, i) => (
            <span key={i} style={{ display: "inline-block", animation: `fadeUp6 360ms var(--ease) both`, animationDelay: `${i * 45}ms` }}>{ch}</span>
          ))}
          <span style={{
            display: "inline-block", width: 6, height: 6, borderRadius: 999,
            background: "var(--accent)", marginLeft: 4,
            animation: "dotPop 420ms var(--ease) both", animationDelay: "760ms",
          }} />
        </div>

        <h1 style={{ fontSize: 34, lineHeight: 1.18, fontWeight: 500, letterSpacing: "-0.025em", textAlign: "center", margin: "0 0 16px", color: "var(--ink)" }}>
          Which accounts deserve a second look?
        </h1>
        <p style={{ fontSize: 15, lineHeight: 1.62, color: "var(--muted)", textAlign: "center", margin: "0 0 32px" }}>
          Ask in plain language; Caseline decides which checks to run.
        </p>

        <div style={{ background: "var(--surface)", border: `1px solid ${focused ? "var(--accent-tint-border)" : "var(--border)"}`, borderRadius: 22, padding: 18, transition: "border-color 120ms" }}>
          <input
            aria-label="Ask a question"
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && draft.trim()) onSend(); }}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder="Find structuring patterns in the last 30 days"
            autoFocus
            style={{ width: "100%", border: "none", background: "transparent", fontSize: 15, lineHeight: 1.62, padding: 0, outline: "none" }}
          />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 14 }}>
            <button onClick={onAbout} style={{ fontSize: 13.5, color: "var(--faint)", transition: "color 120ms" }}>
              {statsLine ? `HI-Small · ${num(statsLine.n_txns)} transactions` : "HI-Small"}
            </button>
            <button aria-label="Send" onClick={onSend} className="hv-accent"
              style={{ width: 34, height: 34, flex: "none", borderRadius: 999, background: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <SendIcon />
            </button>
          </div>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center", marginTop: 20 }}>
          {suggestions.map((s) => (
            <button key={s} onClick={() => onPick(s)} className="hv-violet"
              style={{ display: "inline-flex", alignItems: "center", whiteSpace: "nowrap", minHeight: 36, padding: "7px 16px", borderRadius: 999, border: "1px solid var(--border)", background: "var(--surface)", fontSize: 13.5, color: "var(--muted)" }}>
              {s}
            </button>
          ))}
        </div>

        <div style={{ marginTop: 32, textAlign: "center" }}>
          <button onClick={onAbout} className="label hv-violet"
            style={{ padding: "6px 12px", borderRadius: 999 }}>
            About Caseline · data, method, evals
          </button>
        </div>
      </div>
    </div>
  );
}
