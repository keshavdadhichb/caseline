/* Landing screen: intro wipe, wordmark entrance, typed headline, cycling
   composer placeholder, send ripple, and the problem-statement chips. */

import { useRef, useState } from "react";
import { SendIcon } from "./ui";
import { num } from "../api";
import { useIntroWipe, usePlaceholderCycle, useTypewriter } from "../hooks";
import { MicButton } from "./MicButton";

const WORDMARK = "Caseline";
const HEADLINE = "What should we look into?";

export function Landing({
  draft, onDraft, onSend, suggestions, onPick, onAbout, statsLine, leaving, micOn,
}: {
  draft: string;
  onDraft: (v: string) => void;
  onSend: () => void;
  suggestions: string[];
  onPick: (q: string) => void;
  onAbout: () => void;
  statsLine: { n_txns: number } | null;
  leaving: boolean;
  micOn: boolean;
}) {
  const [focused, setFocused] = useState(false);
  const [rippling, setRippling] = useState(false);
  const dotRef = useRef<HTMLSpanElement>(null);
  const intro = useIntroWipe(dotRef);

  // The headline starts typing once the wipe has converged on the dot.
  const headline = useTypewriter(HEADLINE, { start: intro.running || !intro.visible });
  const placeholder = usePlaceholderCycle(suggestions, focused || draft.length > 0);

  const send = () => {
    if (!draft.trim()) return;
    setRippling(true);
    setTimeout(() => setRippling(false), 700);
    onSend();
  };

  return (
    <>
      {intro.visible && (
        <div
          aria-hidden="true"
          className="intro-cover"
          style={{
            // Stays a full opaque cover until the dot has been measured, then
            // collapses onto it. Starting before the measurement would aim the
            // wipe at a default point and visibly jump when it corrected.
            clipPath: `circle(142% at ${intro.origin?.x ?? "50%"} ${intro.origin?.y ?? "28%"})`,
            animation: intro.running ? "introShrink 900ms var(--ease-in-out) forwards" : undefined,
            ["--ix" as string]: intro.origin?.x ?? "50%",
            ["--iy" as string]: intro.origin?.y ?? "28%",
          }}
        />
      )}

      <div className="scroll" style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{
          width: 620, maxWidth: "calc(100% - 48px)", padding: "40px 0",
          opacity: leaving ? 0 : 1,
          transform: leaving ? "translateY(-14px)" : "none",
          transition: "opacity var(--dur-base) var(--ease-out), transform var(--dur-base) var(--ease-out)",
        }}>
          <div style={{ textAlign: "center", marginBottom: 40, fontSize: 19, fontWeight: 500, letterSpacing: "-0.015em", color: "var(--ink)" }}>
            {WORDMARK.split("").map((ch, i) => (
              <span key={i} style={{ display: "inline-block", animation: "fadeUp6 360ms var(--ease-out) both", animationDelay: `${i * 45}ms` }}>{ch}</span>
            ))}
            {/* The wipe is aimed at THIS wrapper, not the dot itself: the dot
                starts at scale(0) from dotPop's `both` fill, so its own
                bounding box measures zero. The wrapper's layout box is
                unaffected by the child's transform. */}
            <span ref={dotRef} style={{ display: "inline-block", width: 6, height: 6, marginLeft: 4, verticalAlign: "baseline" }}>
              <span style={{
                display: "block", width: 6, height: 6, borderRadius: 999, background: "var(--violet)",
                animation: "dotPop var(--dur-slow) var(--ease-out) both", animationDelay: "760ms",
              }} />
            </span>
          </div>

          <h1 style={{ fontSize: 34, lineHeight: 1.18, fontWeight: 500, letterSpacing: "-0.025em", textAlign: "center", margin: "0 0 16px", color: "var(--ink)", minHeight: 41 }}>
            {headline.text}
            {!headline.done && (
              <span style={{
                display: "inline-block", width: 2, height: "0.82em", background: "var(--violet)",
                marginLeft: 5, verticalAlign: "-0.06em", animation: "caretBlink 1.1s steps(1,end) infinite",
              }} />
            )}
          </h1>
          <p style={{ fontSize: 15, lineHeight: 1.62, color: "var(--ink-2)", textAlign: "center", margin: "0 0 32px" }}>
            Ask in plain language; Caseline decides which checks to run.
          </p>

          <div style={{ background: "var(--surface)", border: `1px solid ${focused ? "var(--violet-tint-border)" : "var(--line)"}`, borderRadius: 22, padding: 18, transition: "border-color var(--dur-micro) ease, background-color var(--dur-fast) ease" }}>
            <input
              aria-label="Ask a question"
              value={draft}
              onChange={(e) => onDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") send(); }}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder={placeholder}
              autoFocus
              style={{ width: "100%", border: "none", background: "transparent", fontSize: 15, lineHeight: 1.62, padding: 0, outline: "none" }}
            />
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 14 }}>
              <button onClick={onAbout} className="hv-theme" style={{ fontSize: 13.5, color: "var(--ink-3)" }}>
                {statsLine ? `HI-Small · ${num(statsLine.n_txns)} transactions` : "HI-Small"}
              </button>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <MicButton enabled={micOn} onText={onDraft} />
                <span style={{ position: "relative", display: "inline-flex" }}>
                {rippling && (
                  <>
                    <span aria-hidden="true" style={{ position: "absolute", inset: 0, borderRadius: 999, border: "1px solid var(--violet)", animation: "ripple 480ms var(--ease-out) forwards" }} />
                    <span aria-hidden="true" style={{ position: "absolute", inset: 0, borderRadius: 999, border: "1px solid var(--lilac)", animation: "ripple 560ms var(--ease-out) forwards", animationDelay: "110ms" }} />
                  </>
                )}
                <button aria-label="Send" onClick={send} className="hv-accent"
                  style={{ width: 34, height: 34, flex: "none", borderRadius: 999, background: "var(--violet)", display: "flex", alignItems: "center", justifyContent: "center", position: "relative" }}>
                  <SendIcon />
                </button>
                </span>
              </span>
            </div>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center", marginTop: 20 }}>
            {suggestions.map((s) => (
              <button key={s} onClick={() => onPick(s)} className="hv-violet"
                style={{ display: "inline-flex", alignItems: "center", whiteSpace: "nowrap", minHeight: 36, padding: "7px 16px", borderRadius: 999, border: "1px solid var(--line)", background: "var(--surface)", fontSize: 13.5, color: "var(--ink-2)" }}>
                {s}
              </button>
            ))}
          </div>

          <div style={{ marginTop: 32, textAlign: "center" }}>
            <button onClick={onAbout} className="label hv-violet" style={{ padding: "6px 12px", borderRadius: 999 }}>
              About Caseline · data, method, evals
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
