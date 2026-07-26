/* Theme persistence and the animation primitives the spec calls for.
   Each hook honours prefers-reduced-motion by settling on its final state
   immediately rather than animating toward it. */

import { useCallback, useEffect, useRef, useState } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "caseline-theme";

const reduced = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Light is the default; the choice persists and is applied on load. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === "undefined") return "light";
    return localStorage.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  return [theme, useCallback(() => setTheme((t) => (t === "light" ? "dark" : "light")), [])];
}

/** Types `text` out character by character. The first few characters land
 *  slower, which reads as deliberate rather than mechanical. */
export function useTypewriter(text: string, { firstChars = 4, slow = 85, fast = 52, start = true } = {}) {
  const [n, setN] = useState(start && !reduced() ? 0 : text.length);
  const done = n >= text.length;

  useEffect(() => {
    if (!start || reduced()) { setN(text.length); return; }
    if (done) return;
    const delay = n < firstChars ? slow : fast;
    const id = setTimeout(() => setN((v) => v + 1), delay);
    return () => clearTimeout(id);
  }, [n, text, start, done, firstChars, slow, fast]);

  return { text: text.slice(0, n), done };
}

/** Cycles suggestion questions through a placeholder: type, hold, delete,
 *  pause, next. Pauses entirely while the field is focused or has content. */
export function usePlaceholderCycle(phrases: string[], paused: boolean) {
  const [idx, setIdx] = useState(0);
  const [n, setN] = useState(0);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (paused || reduced() || phrases.length === 0) return;
    const phrase = phrases[idx % phrases.length];

    if (!deleting && n < phrase.length) {
      const id = setTimeout(() => setN((v) => v + 1), 38);
      return () => clearTimeout(id);
    }
    if (!deleting && n >= phrase.length) {
      const id = setTimeout(() => setDeleting(true), 2000);
      return () => clearTimeout(id);
    }
    if (deleting && n > 0) {
      const id = setTimeout(() => setN((v) => Math.max(0, v - 3)), 16);
      return () => clearTimeout(id);
    }
    const id = setTimeout(() => { setDeleting(false); setIdx((v) => v + 1); }, 350);
    return () => clearTimeout(id);
  }, [n, deleting, idx, paused, phrases]);

  if (paused || reduced()) return phrases[0] ?? "";
  return (phrases[idx % phrases.length] ?? "").slice(0, n);
}

/** Counts a figure up from zero on first appearance only. */
export function useCountUp(target: number, duration = 600) {
  const [value, setValue] = useState(reduced() ? target : 0);
  const started = useRef(false);

  useEffect(() => {
    if (started.current || reduced()) { setValue(target); return; }
    started.current = true;
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / duration);
      const eased = 1 - Math.pow(1 - p, 3); // cubic ease-out
      setValue(target * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return value;
}

/** The landing intro wipe. The cover collapses to the measured centre of
 *  the wordmark's fullstop, so the motion resolves onto the brand mark
 *  rather than an arbitrary point. Runs once per page load. */
export function useIntroWipe(dotRef: React.RefObject<HTMLElement | null>) {
  const [phase, setPhase] = useState<"covering" | "gone">(() => (reduced() ? "gone" : "covering"));
  const [origin, setOrigin] = useState({ x: "50%", y: "28%" });

  useEffect(() => {
    if (phase === "gone") return;
    const el = dotRef.current;
    if (el) {
      const r = el.getBoundingClientRect();
      if (r.width > 0) {
        setOrigin({
          x: `${((r.left + r.width / 2) / window.innerWidth) * 100}%`,
          y: `${((r.top + r.height / 2) / window.innerHeight) * 100}%`,
        });
      }
    }
    const id = setTimeout(() => setPhase("gone"), 1200);
    return () => clearTimeout(id);
  }, [phase, dotRef]);

  return { active: phase === "covering", origin };
}

/** Reveals list items one at a time (execution-plan steps arrive ~520ms apart). */
export function useSequence(total: number, interval = 520, enabled = true) {
  const [shown, setShown] = useState(enabled && !reduced() ? 0 : total);

  useEffect(() => {
    if (!enabled || reduced()) { setShown(total); return; }
    if (shown >= total) return;
    const id = setTimeout(() => setShown((v) => v + 1), interval);
    return () => clearTimeout(id);
  }, [shown, total, interval, enabled]);

  useEffect(() => { if (total < shown) setShown(total); }, [total, shown]);

  return shown;
}

export { reduced as prefersReducedMotion };
