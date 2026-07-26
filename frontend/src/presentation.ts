/* Presentation-layer helpers (Gemini-backed, optional).

   Every one of these degrades: the explanation falls back to a
   deterministic server-side summary, speech falls back to the browser's own
   synthesiser, and the mic simply stays hidden when transcription is not
   available. Nothing here touches the agent or the detection results. */

export interface ExplainResult {
  text: string;
  /** "gemini" when the model wrote it, "deterministic" when it was built
   *  from the case's own figures. Surfaced so the UI can say which. */
  source: "gemini" | "deterministic";
  image_b64: string | null;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const presentation = {
  capabilities: () => fetch("/api/presentation").then((r) => r.json() as Promise<{ gemini: boolean }>),
  explain: (body: { case_id?: string; text?: string; question?: string; with_image?: boolean }) =>
    post<ExplainResult>("/explain", body),
  speak: (text: string) =>
    post<{ audio_b64: string | null; source: string }>("/speak", { text }),
  transcribe: (audio_b64: string, mime_type: string) =>
    post<{ text: string }>("/transcribe", { audio_b64, mime_type }),
};

/** Plays text aloud. Prefers the server voice; falls back to the browser's
 *  built-in synthesiser, which needs no key and works offline. Returns a
 *  stop function. */
export async function readAloud(text: string): Promise<() => void> {
  try {
    const { audio_b64 } = await presentation.speak(text);
    if (audio_b64) {
      const audio = new Audio(`data:audio/wav;base64,${audio_b64}`);
      void audio.play();
      return () => { audio.pause(); audio.currentTime = 0; };
    }
  } catch { /* fall through to the browser voice */ }

  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    window.speechSynthesis.speak(utterance);
    return () => window.speechSynthesis.cancel();
  }
  return () => {};
}

/** Records from the microphone until the returned stop function is called,
 *  then resolves with the transcript. */
export async function recordAndTranscribe(): Promise<{ stop: () => Promise<string> }> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
  recorder.start();

  return {
    stop: () =>
      new Promise<string>((resolve, reject) => {
        recorder.onstop = async () => {
          stream.getTracks().forEach((t) => t.stop());
          try {
            const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
            const buf = await blob.arrayBuffer();
            let binary = "";
            const bytes = new Uint8Array(buf);
            for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
            const { text } = await presentation.transcribe(btoa(binary), blob.type);
            resolve(text);
          } catch (err) { reject(err); }
        };
        recorder.stop();
      }),
  };
}
