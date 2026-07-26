/* Dictation control for either composer. Hidden entirely when transcription
   is unavailable, rather than offering a button that fails on click. */

import { useState } from "react";
import { recordAndTranscribe } from "../presentation";

export function MicButton({ enabled, onText }: { enabled: boolean; onText: (t: string) => void }) {
  const [recorder, setRecorder] = useState<{ stop: () => Promise<string> } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!enabled) return null;

  const toggle = async () => {
    if (recorder) {
      setBusy(true);
      const text = await recorder.stop().catch(() => "");
      setRecorder(null); setBusy(false);
      if (text) onText(text);
      return;
    }
    try { setRecorder(await recordAndTranscribe()); } catch { /* mic permission denied */ }
  };

  return (
    <button
      aria-label={recorder ? "Stop dictating" : "Dictate a question"}
      title={recorder ? "Stop dictating" : "Dictate a question"}
      onClick={toggle}
      disabled={busy}
      className="hv-tint"
      style={{
        width: 34, height: 34, flex: "none", borderRadius: 999,
        border: `1px solid ${recorder ? "var(--violet)" : "var(--line)"}`,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
      <span className="dot" style={{
        background: recorder ? "var(--violet)" : "var(--ink-3)",
        animation: recorder ? "pulseDot 1.4s ease-in-out infinite" : busy ? "dotsPulse 1.2s infinite" : undefined,
      }} />
    </button>
  );
}
