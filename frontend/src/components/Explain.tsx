/* "Explain simply" panel: a plain-language rewrite of a case plus a matching
   illustration. Purely presentational, and honest about its own provenance:
   when the model is unavailable the deterministic server summary is shown
   and labelled as such, rather than the control silently doing less. */

import { useState } from "react";
import { presentation, readAloud, type ExplainResult } from "../presentation";
import { Button } from "./ui";

export function Explain({ caseId, geminiOn }: { caseId: string; geminiOn: boolean }) {
  const [result, setResult] = useState<ExplainResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [speaking, setSpeaking] = useState<(() => void) | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true); setError(null);
    try {
      setResult(await presentation.explain({ case_id: caseId, with_image: geminiOn }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const toggleSpeech = async () => {
    if (speaking) { speaking(); setSpeaking(null); return; }
    if (!result) return;
    const stop = await readAloud(result.text);
    setSpeaking(() => stop);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {!result && (
        <Button variant="outline" onClick={run} disabled={loading}>
          {loading ? "Explaining\u2026" : "Explain simply"}
        </Button>
      )}

      {error && (
        <p style={{ margin: 0, fontSize: 13, color: "var(--sev-high-fg)" }}>
          Could not build an explanation: {error}
        </p>
      )}

      {result && (
        <div style={{ background: "var(--tint)", borderRadius: 14, padding: 16, display: "flex", flexDirection: "column", gap: 12, animation: "fadeUp6 var(--dur-base) var(--ease-out) both" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="label" style={{ marginBottom: 0 }}>In plain terms</span>
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
              {result.source === "gemini" ? "generated" : "from case data"}
            </span>
          </div>

          {result.image_b64 && (
            <img
              src={`data:image/jpeg;base64,${result.image_b64}`}
              alt=""
              style={{ width: "100%", borderRadius: 10, display: "block", border: "1px solid var(--line)" }}
            />
          )}

          <p style={{ margin: 0, fontSize: 14, lineHeight: 1.65, maxWidth: "58ch" }}>{result.text}</p>

          <div style={{ display: "flex", gap: 8 }}>
            <Button variant="outline" onClick={toggleSpeech} style={{ height: 32, fontSize: 13 }}>
              {speaking ? "Stop" : "Read aloud"}
            </Button>
            <Button variant="ghost" onClick={run} style={{ height: 32, fontSize: 13 }}>Regenerate</Button>
          </div>
        </div>
      )}
    </div>
  );
}
